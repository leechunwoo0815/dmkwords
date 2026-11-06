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


def test_log_alert_empty_window_passes() -> None:
    env = os.environ.copy()
    env["LOG_ALERT_SINCE"] = "1 second ago"
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "log_alert.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
