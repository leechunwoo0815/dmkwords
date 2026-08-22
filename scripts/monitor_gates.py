#!/usr/bin/env python3
"""librio 门禁日志实时监控工具。

重构对象（原命令）:
    sleep 500; grep -E "passed|failed|PASS|FAIL|No new|OK:|PASSED" \\
        /tmp/librio_gates6.log | tail -20

重构目标: 轮询增量监控代替 sleep 死等 + 错误检测 + 实时输出 + 结果计数。
规格来源: 专家意见/门禁监控工具规格-20260809.md（唯一需求来源）。

用法示例:
    venv/bin/python scripts/monitor_gates.py                          # 默认监控最新日志
    venv/bin/python scripts/monitor_gates.py --log-file /tmp/librio_gates_test.log \\
        --interval 1 --stats-every 5 --timeout 30
    venv/bin/python scripts/monitor_gates.py --exit-on-error          # 发现失败行立即退出码 1

退出码: 0=GATES DONE 正常完成; 1=--exit-on-error 且发现失败行; 2=日志不存在; 3=超时; 4=工具异常
"""

import argparse
import glob
import os
import re
import signal
import sys
import time

DEFAULT_PATTERN = r"passed|failed|PASS|FAIL|No new|OK:|PASSED"
DEFAULT_DONE_MARKER = "===== GATES DONE ====="
LOG_GLOB = "/tmp/librio_gates_*.log"

RED = "\033[31m"
RESET = "\033[0m"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOFILE = 2
EXIT_TIMEOUT = 3
EXIT_BUG = 4

SEGMENT_RE = re.compile(r"^=====\s*([^=]+?)\s*=====$")
# 规格 §一 枚举的全部段名（含真实日志变体）；白名单拒绝 pytest 横幅等垃圾段入清单
KNOWN_SEGMENTS = frozenset(
    {
        "PYTEST",
        "BEHAVE",
        "RUFF",
        "CONTRACT",
        "MODEL",
        "WIRING",
        "CONFIG",
        "INTEGRATION",
        "ALEMBIC",
        "ALEMBIC CHECK",
        "MYSQL",
        "MYSQL CONCURRENCY",
    }
)
# 数字与动词之间允许名词（如 "211 scenarios passed"）
PASSED_RE = re.compile(r"(\d+)\s+(?:\w+\s+)*passed")
FAILED_RE = re.compile(r"(\d+)\s+(?:\w+\s+)*failed")
# pytest/behave 汇总行的小写 error 计数（collection error 等，规格补强防假绿）
ERRORS_RE = re.compile(r"(\d+)\s+errors?\b")


class GateMonitor:
    """单行分类器: 结果行匹配 + 错误检测 + 计数 + 段名收集。"""

    def __init__(self, pattern=DEFAULT_PATTERN, done_marker=DEFAULT_DONE_MARKER):
        self.pattern = re.compile(pattern)
        self.done_marker = done_marker
        self.counters = {
            "passed": 0,
            "failed": 0,
            "pass_rows": 0,
            "fail_rows": 0,
            "ok_rows": 0,
            "no_new": 0,
        }
        self.segments: list[str] = []
        self.done = False

    def process_line(self, line: str) -> tuple[bool, bool, bool]:
        """处理一行日志。返回 (is_result_match, is_error, is_done)。

        错误检测独立于结果行匹配: ERROR/Traceback/failed 等失败信号即使
        不命中 --pattern 也必须被识别（规格 §二.2）。
        """
        line = line.rstrip("\n")
        if line.strip() == self.done_marker:
            self.done = True
            return False, False, True
        m = SEGMENT_RE.match(line)
        if m and m.group(1) in KNOWN_SEGMENTS:
            if m.group(1) not in self.segments:
                self.segments.append(m.group(1))
            return False, False, False
        is_error = self._classify(line)
        if not self.pattern.search(line):
            return False, is_error, False
        return True, is_error, False

    def _classify(self, line: str) -> bool:
        """对结果行做错误判定并更新计数。返回是否错误行。"""
        error = False
        if line.startswith("[FAIL]"):
            self.counters["fail_rows"] += 1
            return True
        if line.startswith("[PASS]"):
            self.counters["pass_rows"] += 1
            return False
        if line.startswith("OK:"):
            self.counters["ok_rows"] += 1
        if "No new" in line:
            self.counters["no_new"] += 1
        for m in PASSED_RE.finditer(line):
            self.counters["passed"] += int(m.group(1))
        for m in FAILED_RE.finditer(line):
            self.counters["failed"] += int(m.group(1))
        # 错误判定规则（规格 §二.2，任一命中即失败）:
        #   N failed 且 N>0；failed/FAIL 文本（无数字或非 0 failed）；ERROR；Traceback
        if any(int(m.group(1)) > 0 for m in FAILED_RE.finditer(line)):
            error = True
        if (
            not FAILED_RE.search(line)
            and re.search(r"failed|FAIL", line, re.I)
            and not re.search(r"passed|PASSED", line, re.I)
        ):
            error = True
        if "ERROR" in line or "Traceback" in line:
            error = True
        if any(int(m.group(1)) > 0 for m in ERRORS_RE.finditer(line)):
            error = True
        if "Interrupted" in line:
            error = True
        return error

    def summary_line(self) -> str:
        c = self.counters
        return (
            f"[summary] passed={c['passed']} failed={c['failed']} "
            f"PASS={c['pass_rows']} FAIL={c['fail_rows']} "
            f"OK={c['ok_rows']} No-new={c['no_new']}"
        )

    def segments_line(self) -> str:
        return "[summary] 段: " + " ".join(s + "✓" for s in self.segments)


def latest_log_file(glob_pattern: str = LOG_GLOB) -> str | None:
    """取修改时间最新的门禁日志；无匹配返回 None。"""
    files = glob.glob(glob_pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def list_candidates() -> str:
    candidates = sorted(glob.glob("/tmp/librio_gates*"))
    return ", ".join(candidates) if candidates else "(无候选文件)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="monitor_gates",
        description="librio 门禁日志实时监控（轮询增量 + 错误检测 + 结果计数）",
    )
    ap.add_argument(
        "--log-file",
        default=None,
        help="门禁日志路径；默认取 /tmp/librio_gates_*.log 中修改时间最新的一个",
    )
    ap.add_argument("--pattern", default=DEFAULT_PATTERN, help="结果行匹配正则（re.search）")
    ap.add_argument("--done-marker", default=DEFAULT_DONE_MARKER, help="完成标记，检测到即汇总退出")
    ap.add_argument("--interval", type=float, default=5, help="轮询间隔（秒）")
    ap.add_argument(
        "--stats-every",
        type=float,
        default=60,
        help="累计统计打印间隔（秒）；0=仅退出时打印",
    )
    ap.add_argument("--timeout", type=float, default=900, help="超时（秒），超时未完成 → 退出码 3")
    ap.add_argument(
        "--tail-lines",
        type=int,
        default=0,
        help="启动时先回扫文件末尾 N 行纳入首轮统计；0=不回扫",
    )
    ap.add_argument("--exit-on-error", action="store_true", help="发现失败行立即退出（退出码 1）")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    use_color = sys.stdout.isatty()

    def out(line: str, fail: bool = False) -> None:
        if fail and use_color:
            line = RED + line + RESET
        print(line, flush=True)

    log_path = args.log_file or latest_log_file()
    if log_path is None:
        print(
            f"[error] 未找到门禁日志（匹配 {LOG_GLOB} 无文件），候选: {list_candidates()}",
            file=sys.stderr,
        )
        return EXIT_NOFILE
    if not os.path.exists(log_path):
        print(
            f"[error] 日志文件不存在: {log_path}，候选: {list_candidates()}",
            file=sys.stderr,
        )
        return EXIT_NOFILE

    monitor = GateMonitor(args.pattern, args.done_marker)
    seen_error = False
    start = time.monotonic()
    last_stats = start
    total_read = 0
    matched_total = 0

    def finish() -> int:
        print(monitor.summary_line())
        print(monitor.segments_line())
        return EXIT_ERROR if (args.exit_on_error and seen_error) else EXIT_OK

    def handle_sigint(_sig, _frame) -> None:
        print("\n[interrupt] 收到 Ctrl+C，当前累计统计:")
        print(monitor.summary_line())
        print(monitor.segments_line())
        sys.exit(EXIT_ERROR if seen_error else EXIT_OK)

    signal.signal(signal.SIGINT, handle_sigint)

    def scan_lines(lines) -> int | None:
        """处理一批行；返回 None 继续，或退出码（DONE=finish / exit-on-error 命中）。"""
        nonlocal seen_error, matched_total
        for ln in lines:
            is_match, is_error, is_done = monitor.process_line(ln)
            if is_done:
                return finish()
            if is_match:
                matched_total += 1
            if is_error:
                seen_error = True
                out(f"[{time.strftime('%H:%M:%S')}] [FAIL] {ln.rstrip()}", fail=True)
                if args.exit_on_error:
                    return EXIT_ERROR
            elif is_match:
                out(f"[{time.strftime('%H:%M:%S')}] {ln.rstrip()}")
        return None

    def read_available(f) -> list[str]:
        """读取并消费所有完整行；遇末尾无换行的半行则回退偏移留给下轮拼接。

        写端（pytest/behave 块缓冲）flush 边界可断在任意字节，readline 会把
        半行直接消费掉导致计数损坏/DONE 丢失（专家审查 P2-1）。
        """
        lines = []
        while True:
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                f.seek(f.tell() - len(raw))
                break
            lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
        return lines

    try:
        f = open(log_path, "rb")
        ino = os.fstat(f.fileno()).st_ino
        f.seek(0, os.SEEK_END)

        # 启动回扫: --tail-lines 取末尾 N 行；--exit-on-error 需立即发现已有失败行，回扫全部
        if args.tail_lines > 0 or args.exit_on_error:
            f.seek(0)
            existing = read_available(f)
            if args.tail_lines > 0:
                existing = existing[-args.tail_lines :]
            total_read += len(existing)
            rc = scan_lines(existing)
            if rc is not None:
                return rc

        stats_window = 0
        while True:
            now = time.monotonic()
            if now - start > args.timeout:
                print(
                    f"[error] 超时未完成（timeout={args.timeout:.0f}s）",
                    file=sys.stderr,
                )
                print(monitor.summary_line())
                print(monitor.segments_line())
                return EXIT_TIMEOUT
            # 处理轮转/重建: inode 变化（mv 重建）或 size 小于已读偏移（同 inode 截断，规格 §三）
            try:
                st = os.stat(log_path)
            except FileNotFoundError:
                print(f"[error] 监控期间日志文件消失: {log_path}", file=sys.stderr)
                print(monitor.summary_line())
                print(monitor.segments_line())
                return EXIT_NOFILE
            if st.st_ino != ino or st.st_size < f.tell():
                f.close()
                f = open(log_path, "rb")
                ino = os.fstat(f.fileno()).st_ino

            period_lines = 0
            for ln in read_available(f):
                period_lines += 1
                rc = scan_lines([ln])
                if rc is not None:
                    return rc
            total_read += period_lines
            stats_window += period_lines
            # 周期统计（窗口累计，非最近单轮）
            if args.stats_every > 0 and now - last_stats >= args.stats_every:
                print(
                    f"[stats] period=+{stats_window} total={total_read}"
                    f" matched={matched_total}"
                    f" passed={monitor.counters['passed']}"
                    f" failed={monitor.counters['failed']}"
                )
                stats_window = 0
                last_stats = now
            time.sleep(args.interval)
    except KeyboardInterrupt:  # 理论不可达（SIGINT handler 接管），防御兜底
        print("\n[interrupt] 收到 Ctrl+C")
        print(monitor.summary_line())
        print(monitor.segments_line())
        return EXIT_ERROR if seen_error else EXIT_OK
    except Exception as exc:  # noqa: BLE001 - 工具异常统一退出码 4
        print(f"[error] 工具异常: {exc}", file=sys.stderr)
        return EXIT_BUG
    finally:
        try:
            f.close()
        except NameError:
            pass


if __name__ == "__main__":
    sys.exit(main())
