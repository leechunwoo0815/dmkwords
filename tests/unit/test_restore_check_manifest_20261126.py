# tests/unit/test_restore_check_manifest_20261126.py — B2-3 恢复演练改对 manifest（2026-11-26 双审查）
"""问题：`restore_check.sh` 原先拿"恢复出来的临时库"跟**此刻的生产库**逐表比行数。
生产每秒都在写（`system_metrics` 每 60 秒一行），备份却是几小时前的快照——两边天然不同，
于是演练要么永远报假差异、要么被当成噪音忽略。**对不上的校验等于没有校验。**

修法：`backup_db.sh` 在备份时写下 `<dump>.manifest`（created_at / sha256 / 每表行数 /
易变表名单），`restore_check.sh` 恢复后**对 manifest 比**：

  · 稳定表：行数必须**逐表相等**（差一行就是恢复不完整）；
  · 易变表（采样/审计/任务日志/短信码/通知）：只要求「恢复值 ≤ 生产现在」——
    备份之后新增的行会让等值比较必红，但恢复出"备份之后才有的行"是真异常；
  · 备份 sha256 与 manifest 不符 → 文件被替换/损坏，拒绝演练；
  · **没有 manifest 就退出 2**（不许假绿），并给可执行指引。

本文件用**假 mysql 客户端**（PATH 前置）驱动全部判定，不连真库、不需要 root。
"""

from __future__ import annotations

import gzip
import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
RESTORE_CHECK = SCRIPTS / "restore_check.sh"

#: 假 mysql 只认这三类查询；行数从环境变量里的"表=行数"清单取
FAKE_MYSQL = r"""#!/bin/bash
# 假 mysql：只实现 restore_check.sh 用到的三类查询
sql=""
for a in "$@"; do
  case "$a" in
    -e) next_sql=1 ;;
    *)
      if [ "${next_sql:-0}" = "1" ]; then sql="$a"; next_sql=0; fi ;;
  esac
done

if [ -z "$sql" ]; then cat > /dev/null; exit 0; fi   # 导入调用：吃掉 stdin

lookup() {  # $1 = 清单环境变量名，$2 = 表名
  local list="${!1:-}" item
  for item in $list; do
    case "$item" in
      "$2="*) echo "${item#*=}"; return 0 ;;
    esac
  done
  echo 0
}

case "$sql" in
  *information_schema.tables*)
    echo "${FAKE_TABLE_COUNT:-2}"
    ;;
  *COUNT*)
    db="$(printf '%s' "$sql" | sed -n 's/.*FROM `\([A-Za-z0-9_]*\)`\.`\([A-Za-z0-9_]*\)`.*/\1 \2/p')"
    [ -n "$db" ] || { echo 0; exit 0; }
    set -- $db
    if [ "$1" = "${FAKE_RESTORE_DB:-dmkwords_restore_check}" ]; then
      lookup FAKE_RESTORE_COUNTS "$2"
    else
      lookup FAKE_PROD_COUNTS "$2"
    fi
    ;;
  *) exit 0 ;;
esac
"""


def _env(tmp_path: Path, *, manifest: str | None, restore_counts: str, prod_counts: str) -> dict:
    """造一份 dump + （可选）manifest + 假 mysql，返回子进程环境。"""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dump = backup_dir / "dmkwords_full_20261126_043000.sql.gz"
    with gzip.open(dump, "wt") as fh:
        fh.write("-- fake dump\n-- Dump completed on 2026-11-26  4:30:00\n")

    if manifest is not None:
        (backup_dir / f"{dump.name}.manifest").write_text(manifest)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    mysql = fake_bin / "mysql"
    mysql.write_text(FAKE_MYSQL)
    mysql.chmod(0o755)
    cnf = tmp_path / "root.cnf"
    cnf.write_text("[client]\nuser=root\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "BACKUP_DIR": str(backup_dir),
            "DB_NAME": "dmkwords",
            "RESTORE_DB": "dmkwords_restore_check",
            "RESTORE_CNF": str(cnf),
            "FAKE_RESTORE_DB": "dmkwords_restore_check",
            "FAKE_RESTORE_COUNTS": restore_counts,
            "FAKE_PROD_COUNTS": prod_counts,
            "FAKE_TABLE_COUNT": "2",
        }
    )
    return env, dump


def _run(env: dict, dump: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RESTORE_CHECK), str(dump)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _manifest(dump: Path, *, rows: str, volatile: str = "system_metrics") -> str:
    import hashlib

    sha = hashlib.sha256(dump.read_bytes()).hexdigest()
    return (
        "# DmkWords backup manifest\n"
        "created_at=2026-11-26 04:30:00 +0800\n"
        "db=dmkwords\n"
        "tables=2\n"
        f"volatile={volatile}\n"
        f"sha256={sha}\n"
        "--- row counts at dump time ---\n"
        f"{rows}"
    )


def test_restore_check_refuses_without_manifest(tmp_path: Path):
    """没有 manifest → 退出 2 并给指引（不许拿"和生产现在比"冒充一致性证据）。"""
    env, dump = _env(tmp_path, manifest=None, restore_counts="orders=5", prod_counts="orders=5")
    proc = _run(env, dump)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "缺少 manifest" in proc.stdout
    assert "拒绝给假结论" in proc.stdout


def test_restore_check_passes_against_manifest(tmp_path: Path):
    """稳定表逐表相等 + 易变表 ≤ 现值 → 通过。"""
    env, dump = _env(
        tmp_path,
        manifest=None,
        restore_counts="orders=5 system_metrics=100",
        prod_counts="orders=9 system_metrics=140",
    )
    (dump.parent / f"{dump.name}.manifest").write_text(
        _manifest(dump, rows="orders 5\nsystem_metrics 100\n")
    )
    proc = _run(env, dump)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "恢复演练通过" in proc.stdout
    assert "易变表" in proc.stdout, "易变表必须显式说明只校验 ≤ 现值"


def test_restore_check_fails_on_stable_row_mismatch(tmp_path: Path):
    """稳定表差一行 = 恢复不完整，必须判红（这正是原来发现不了的场景）。"""
    env, dump = _env(
        tmp_path,
        manifest=None,
        restore_counts="orders=4 system_metrics=100",
        prod_counts="orders=4 system_metrics=140",
    )
    (dump.parent / f"{dump.name}.manifest").write_text(
        _manifest(dump, rows="orders 5\nsystem_metrics 100\n")
    )
    proc = _run(env, dump)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "manifest=5 恢复=4" in proc.stdout


def test_restore_check_fails_when_backup_sha_changed(tmp_path: Path):
    """dump 与 manifest 的 sha256 不符（文件被替换/损坏）→ 拒绝演练。"""
    env, dump = _env(
        tmp_path,
        manifest=None,
        restore_counts="orders=5",
        prod_counts="orders=5",
    )
    bad = _manifest(dump, rows="orders 5\n").replace(
        # 换成一个"看起来合法但不是这份文件"的 sha
        next(
            line
            for line in _manifest(dump, rows="orders 5\n").splitlines()
            if line.startswith("sha256=")
        ),
        "sha256=" + "0" * 64,
    )
    (dump.parent / f"{dump.name}.manifest").write_text(bad)
    proc = _run(env, dump)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "sha256 不符" in proc.stdout


def test_restore_check_flags_volatile_rows_beyond_now(tmp_path: Path):
    """易变表"恢复值 > 生产现值" = 恢复出了备份之后才有的行，属真异常。"""
    env, dump = _env(
        tmp_path,
        manifest=None,
        restore_counts="orders=5 system_metrics=999",
        prod_counts="orders=9 system_metrics=140",
    )
    (dump.parent / f"{dump.name}.manifest").write_text(
        _manifest(dump, rows="orders 5\nsystem_metrics 100\n")
    )
    proc = _run(env, dump)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "恢复=999 > 现值=140" in proc.stdout


# 假 mysql / mysqldump：让 backup_db.sh 真跑一遍（而不是断言源码里有某个字符串）
FAKE_MYSQL_BACKUP = r"""#!/bin/bash
# 假 mysql：只回答 backup_db.sh 用到的两类查询
sql=""
next=0
for a in "$@"; do
  case "$a" in
    -e) next=1 ;;
    *) if [ "$next" = "1" ]; then sql="$a"; next=0; fi ;;
  esac
done
if [ -z "$sql" ]; then cat > /dev/null; exit 0; fi
# 注意 SQL 里 COUNT 在 information_schema **之前**（SELECT COUNT(*) FROM information_schema...），
# 所以表数量那条的模式是 COUNT*information_schema，写反了就会把表数当成行数
case "$sql" in
  *table_name*information_schema*) printf '%s\n' orders system_metrics ;;
  *COUNT*information_schema*) echo "${FAKE_TABLE_COUNT:-2}" ;;
  *COUNT*) echo "${FAKE_ROWS:-7}" ;;
  *) echo "" ;;
esac
"""

FAKE_MYSQLDUMP = r"""#!/bin/bash
# 假 mysqldump：输出 2 个 CREATE TABLE + 标准结束标记（backup_db.sh 会校验这两点）
cat <<'DUMP'
CREATE TABLE `orders` (id int);
CREATE TABLE `system_metrics` (id int);
-- Dump completed on 2026-11-26  4:30:00
DUMP
"""


def _parse_manifest(path: Path) -> dict:
    """把 manifest 解析成结构化字段（不靠子串断言——那是"读源码文本"，不是行为）。"""
    header: dict[str, str] = {}
    rows: dict[str, int] = {}
    in_rows = False
    for line in path.read_text().splitlines():
        if line.startswith("--- row counts"):
            in_rows = True
            continue
        if not line.strip() or line.startswith("#"):
            continue
        if in_rows:
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                rows[parts[0]] = int(parts[1])
        elif "=" in line:
            k, v = line.split("=", 1)
            header[k] = v
    header["rows"] = rows
    return header


def _backup_env(tmp_path: Path) -> dict:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (("mysql", FAKE_MYSQL_BACKUP), ("mysqldump", FAKE_MYSQLDUMP)):
        f = fake_bin / name
        f.write_text(body)
        f.chmod(0o755)
    cnf = tmp_path / "backup.cnf"
    cnf.write_text("[client]\nuser=dmkwords\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "MYSQL_CNF": str(cnf),
            "DB_NAME": "dmkwords",
            "FAKE_TABLE_COUNT": "2",
            "FAKE_ROWS": "7",
        }
    )
    env.pop("BACKUP_UPLOADS", None)
    return env


def test_backup_script_writes_manifest_next_to_dump(tmp_path: Path):
    """真跑一次备份：dump 旁边必须出现 manifest，sha256 必须与 dump 实际一致。"""
    import hashlib

    env = _backup_env(tmp_path)
    env["PROJECT_DIR"] = str(tmp_path)  # 阻止脚本读取仓库里的 uploads/
    proc = subprocess.run(
        ["bash", str(SCRIPTS / "backup_db.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    dumps = sorted((tmp_path / "backups").glob("dmkwords_full_*.sql.gz"))
    assert dumps, f"没有产出备份文件：{proc.stdout}"
    manifest = dumps[-1].with_name(dumps[-1].name + ".manifest")
    assert manifest.exists(), f"备份旁没有 manifest：{proc.stdout}"
    fields = _parse_manifest(manifest)
    assert {"system_metrics", "audit_logs"} <= set(fields["volatile"].split()), (
        f"易变表名单不完整：{fields['volatile']}"
    )
    assert fields["sha256"] == hashlib.sha256(dumps[-1].read_bytes()).hexdigest(), (
        "manifest 的 sha256 必须对应这份 dump"
    )
    assert fields["rows"] == {"orders": 7, "system_metrics": 7}, f"行数段不对：{fields['rows']}"


def test_backup_prune_removes_manifest_with_dump(tmp_path: Path):
    """过期清理必须连 manifest 一起删（否则 backups 目录留下一堆孤儿清单）。"""
    env = _backup_env(tmp_path)
    env["PROJECT_DIR"] = str(tmp_path)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # 造一份"14 天前"的旧备份 + 它的 manifest
    old = backup_dir / "dmkwords_full_20260101_043000.sql.gz"
    old.write_bytes(b"old")
    old_manifest = old.with_name(old.name + ".manifest")
    old_manifest.write_text("sha256=x\n")
    stale = 1_700_000_000  # 2023-11：远超 RETENTION_DAYS
    os.utime(old, (stale, stale))
    os.utime(old_manifest, (stale, stale))
    env["BACKUP_RETENTION_DAYS"] = "14"

    proc = subprocess.run(
        ["bash", str(SCRIPTS / "backup_db.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not old.exists(), "过期 dump 必须被清掉"
    assert not old_manifest.exists(), "过期 dump 的 manifest 必须一起清掉（不许留孤儿）"
