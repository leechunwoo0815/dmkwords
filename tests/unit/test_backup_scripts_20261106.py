# tests/unit/test_backup_scripts_20261106.py — 备份/恢复脚本静态判据
#
# 背景（2026-11-06 生产首发实测两个坑）：
#   1) 生产账号没有全局 PROCESS，`mysqldump` 缺 `--no-tablespaces` 会报
#      "Access denied ... PROCESS" 并非零退出，旧脚本还会留下**截断的 .gz**
#      （`gzip -t` 只校验压缩流，不校验 SQL 完整性）；
#   2) 旧 binlog 脚本在宿主 MySQL 上既没有 REPLICATION CLIENT（SHOW MASTER STATUS 失败），
#      也读不到 `/var/lib/mysql`（640 mysql:mysql）。
#
# 这些是 bash 脚本，不 import；用文本判据 + `bash -n` + 安全阀实跑兜住。

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_backup_db_has_required_flags() -> None:
    text = _read("backup_db.sh")
    for token in (
        "--single-transaction",
        "--quick",
        "--no-tablespaces",
        "--set-gtid-purged=OFF",
        "Dump completed",
        "gzip -t",
        "CREATE TABLE",
        ".tmp_",
        'mkdir "$LOCK_DIR"',
    ):
        assert token in text, f"backup_db.sh 缺少 {token}"


def test_backup_binlog_uses_remote_mode_and_alarms() -> None:
    text = _read("backup_binlog.sh")
    for token in (
        "--read-from-remote-server",
        "--raw",
        "SHOW BINARY LOGS",
        "FLUSH BINARY LOGS",
        "exit 2",
    ):
        assert token in text, f"backup_binlog.sh 缺少 {token}"


def test_restore_check_has_safety_valves() -> None:
    text = _read("restore_check.sh")
    assert "restore_check" in text
    assert "不能等于业务库" in text
    assert "DROP DATABASE IF EXISTS" in text


def test_scripts_parse() -> None:
    for name in ("backup_db.sh", "backup_binlog.sh", "restore_check.sh"):
        subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=True)


def _run_restore_check(restore_db: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RESTORE_DB"] = restore_db
    return subprocess.run(
        ["bash", str(SCRIPTS / "restore_check.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_restore_check_refuses_production_db_name() -> None:
    proc = _run_restore_check("dmkwords")
    assert proc.returncode == 2
    assert "restore_check" in proc.stdout + proc.stderr


def test_restore_check_refuses_unsafe_name() -> None:
    proc = _run_restore_check("dmkwords_restore_check;drop")
    assert proc.returncode == 2
    assert "只允许" in proc.stdout + proc.stderr


def test_backup_status_alarms_when_missing(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(tmp_path)
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "backup_status.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 2
    assert "无全量备份" in proc.stdout


def test_log_alert_covers_deadlock_and_traceback() -> None:
    text = _read("log_alert.sh")
    for token in ("1213", "Deadlock found", "Traceback", "500 Internal Server Error"):
        assert token in text, f"log_alert.sh 缺少 {token}"


def _fake_sudo(
    tmp_path: Path, *, journal_ok: bool, journal_rc: int = 0, mysql_ok: bool = True
) -> str:
    """造一个只认 `-n journalctl|tail|stat` 的假 sudo，返回要前置到 PATH 的目录。

    为什么用假 sudo 而不是"在本机跑真实 journalctl"：真实环境里 sudo 权限因机器而异，
    断言只能写成 `in (0, 3)` —— 那就等于没钉住判据。假 sudo 让三条路径都确定可复现。
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    journal = (
        "echo \"$(date '+%b %e %H:%M:%S') host dmkwords-backend[1]: INFO hello\"\n    exit 0\n"
        if journal_ok
        else f'echo "sudo: a password is required" >&2\n    exit {journal_rc or 1}\n'
    )
    mysql = (
        'echo "2026-11-26T10:00:00.000000+08:00 0 [Note] mysqld: ready for connections"\n    exit 0\n'
        if mysql_ok
        else 'echo "tail: cannot open" >&2\n    exit 1\n'
    )
    script = (
        "#!/bin/bash\n"
        "shift  # 丢掉 -n\n"
        'case "$1" in\n'
        "  journalctl)\n    " + journal + "  ;;\n"
        "  tail)\n    " + mysql + "  ;;\n"
        "  stat) echo 1700000000; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    sudo = bin_dir / "sudo"
    sudo.write_text(script)
    sudo.chmod(0o755)
    return str(bin_dir)


def _run_log_alert(fake_bin: str, since: str = "24 hours ago") -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = fake_bin + os.pathsep + env.get("PATH", "")
    env["LOG_ALERT_SINCE"] = since
    env.pop("ALERT_WEBHOOK", None)
    env.pop("BACKUP_ALERT_WEBHOOK", None)
    return subprocess.run(
        ["bash", str(SCRIPTS / "log_alert.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_log_alert_fails_closed_when_journal_unreadable(tmp_path: Path) -> None:
    """B2-2（2026-11-26 双审查）：**读不到日志 ≠ 没有异常**。

    原脚本对 `journalctl`/MySQL 错误日志一律 `|| true`，把"没权限 / unit 名写错 / 日志被清"
    压成空串 → 三项计数 0 → 打印"✅ 巡检正常"退出 0。监控瞎了还报平安，比不做监控更坏。
    新判据：探测源不可用 → 退出码 **3**（0=正常 / 2=有异常 / 3=无法巡检）。
    """
    proc = _run_log_alert(_fake_sudo(tmp_path, journal_ok=False))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "无法巡检" in proc.stdout
    assert "巡检正常" not in proc.stdout, "探测源不可用时不许输出“正常”"


def test_log_alert_fails_closed_on_empty_window(tmp_path: Path) -> None:
    """journalctl 成功但窗口内 0 条真实日志行（unit 名错/服务没起）→ 同样判 3。"""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(
        "#!/bin/bash\nshift\ncase \"$1\" in\n  journalctl) echo '-- No entries --'; exit 0 ;;\n"
        "  tail) echo 'mysqld ready'; exit 0 ;;\n  stat) echo 1700000000; exit 0 ;;\nesac\nexit 0\n"
    )
    sudo.chmod(0o755)
    proc = _run_log_alert(str(bin_dir))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "0 条" in proc.stdout


def test_log_alert_fails_closed_when_mysql_log_empty(tmp_path: Path) -> None:
    """MySQL 错误日志为空 = 死锁探测失效 → 判 3（不许当"零死锁"）。"""
    proc = _run_log_alert(_fake_sudo(tmp_path, journal_ok=True, mysql_ok=False))
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "MySQL 错误日志" in proc.stdout


def test_log_alert_passes_when_sources_healthy(tmp_path: Path) -> None:
    """对照组：两个探测源都正常且无异常 → 退出 0（别把正常路径也拦死）。"""
    proc = _run_log_alert(_fake_sudo(tmp_path, journal_ok=True, mysql_ok=True))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "日志巡检正常" in proc.stdout
    assert "死锁 0" in proc.stdout
