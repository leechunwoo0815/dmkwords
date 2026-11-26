# tests/unit/test_process_stop_20261126.py — 停进程的等待语义（复审 N1，2026-11-26）
"""复审复现的事故：`dev.sh restart` = `stop; start`，而 old `stop()` 发完 SIGTERM 立刻返回并删 pid 文件。
uvicorn 在还有 keep-alive/长连接时**不会马上退**（实测数分钟），于是：

  ① `start()` 的端口防呆看到 8002 仍被占 → 报"端口被占用"退出（重启用不了）；
  ② 或健康检查打到**旧进程**上 → 假绿（`check_demo_baseline` 内部那次 restart 确定性假红）。

修法 = `scripts/lib/process_stop.sh`：SIGTERM → **等它真退**（默认 10 秒）→ 超时才 SIGKILL → 再等端口释放。
本文件用真实的 `sleep` 进程驱动这两个函数（不碰 8002/5173，不依赖 dev.sh）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[2] / "scripts" / "lib" / "process_stop.sh"


def _run_snippet(snippet: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """在 bash 里 source 库后执行片段（片段自己打印结果）。"""
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n{snippet}'],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_cooperative_process_exits_quickly():
    """正常响应 SIGTERM 的进程：应在数秒内退，且不需要 SIGKILL。"""
    proc = _run_snippet(
        "sleep 60 & pid=$!\n"
        "start=$(date +%s)\n"
        'stop_pid_gracefully "$pid" 5 && echo "exit=ok"\n'
        "end=$(date +%s)\n"
        'echo "elapsed=$((end-start))"\n'
        'kill -0 "$pid" 2>/dev/null && echo "still_alive=1" || echo "still_alive=0"\n'
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "exit=ok" in proc.stdout
    assert "still_alive=0" in proc.stdout, "必须确认进程真的没了，而不是发完信号就返回"
    elapsed = int(proc.stdout.split("elapsed=")[1].split()[0])
    assert elapsed <= 5, f"正常进程不该等满超时：{elapsed}s"


def test_stubborn_process_is_killed_after_timeout():
    """忽略 SIGTERM 的进程：超时后必须 SIGKILL 收场（这才是"确定退出了"）。"""
    proc = _run_snippet(
        # trap '' TERM = 明确忽略 SIGTERM，模拟"卡住不退"的 uvicorn。
        # exec 让 sleep 顶替 shell（否则 SIGKILL 只杀 shell，孤儿 sleep 会吊住 stdout 把用例挂死）
        "bash -c 'trap \"\" TERM; exec sleep 60' >/dev/null 2>&1 & pid=$!\n"
        "sleep 0.3\n"
        "start=$(date +%s)\n"
        'stop_pid_gracefully "$pid" 1\n'
        "end=$(date +%s)\n"
        'echo "elapsed=$((end-start))"\n'
        'kill -0 "$pid" 2>/dev/null && echo "still_alive=1" || echo "still_alive=0"\n'
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SIGKILL" in proc.stdout, "超时必须打出降级说明（运维要看到）"
    assert "still_alive=0" in proc.stdout, "SIGKILL 后必须确认进程已消失"
    elapsed = int(proc.stdout.split("elapsed=")[1].split()[0])
    assert elapsed >= 1, "至少等满超时才降级"
    assert elapsed <= 8, f"SIGKILL 后不该再等这么久：{elapsed}s"


def test_already_dead_pid_is_noop():
    """pid 早就不在：立即返回 0（restart 期间被外部 kill 的情况）。"""
    proc = _run_snippet(
        "sleep 0.1 & pid=$!\n"
        "wait $pid 2>/dev/null || true\n"
        'stop_pid_gracefully "$pid" 3 && echo "noop-ok"\n'
        'echo "done"\n'
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "noop-ok" in proc.stdout
    assert "SIGKILL" not in proc.stdout, "已经死了的进程不该触发降级"


def test_wait_port_free_returns_when_port_is_free():
    """端口本来就空：立即成功（不白等）。"""
    proc = _run_snippet('wait_port_free 59 = 2>/dev/null; wait_port_free 59999 3 && echo "free-ok"')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "free-ok" in proc.stdout


def test_wait_port_free_times_out_on_listening_port():
    """有 LISTEN 占用：必须等满超时并返回非 0（让上层能报出来，而不是静默继续）。"""
    proc = _run_snippet(
        # 用一个临时 python 起监听，端口挑高位避免撞本机服务
        "python3 -m http.server 53177 --bind 127.0.0.1 >/dev/null 2>&1 & srv=$!\n"
        "sleep 1\n"
        'if wait_port_free 53177 2; then echo "unexpectedly-free"; else echo "timeout-ok"; fi\n'
        "kill $srv 2>/dev/null || true\n"
    )
    assert "timeout-ok" in proc.stdout, proc.stdout + proc.stderr
