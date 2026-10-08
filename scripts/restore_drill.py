# scripts/restore_drill.py — 备份/恢复演练（docs/20 §七 P13 验收项）
"""演练两件事，都**只在演练库上做**，绝不碰业务库：

1. `full`：全量备份 → 演练库恢复 → **五表行数对拍**（books/parents/children/orders/activities）
   + 表数/迁移版本对拍 → 冒烟（起后端 health + 演示账号登录）
2. `pitr`：灾难 + **binlog 点对点恢复**——剧本：记位置锚点 → 写入探针书（业务活动）→ 误删
   → `DROP DATABASE`（灾难）→ 恢复全量 + 重放 binlog 至"写入后、误删前" → 断言探针书回来、误删没被重放

为什么走 `docker exec`：部署/开发机上不一定装了 mysql 客户端（本机就没有：mysqldump/mysql/
mysqlbinlog 全部缺失），容器里自带全套工具，也和生产跑法一致（容器名见 `docker-compose.prod.yml`）。
**生产机若直接跑 `scripts/backup_db.sh`，需先装 mysql-client，或把 crontab 包一层 docker exec**
（已记入 `docs/20` §七）。凭据只经环境变量进容器（`docker exec -e MYSQL_PWD`），不进 argv、不打印。

用法（**默认 dry-run**，只打印计划；`APPLY=1` 才真跑）：
    python -m scripts.restore_drill full
    APPLY=1 python -m scripts.restore_drill full
    APPLY=1 DROP_AFTER=1 python -m scripts.restore_drill pitr

环境变量：
    DUMP_FILE          全量备份文件（默认现场生成一份，落到 backups/）
    SOURCE_CONTAINER   源库容器（默认 dmkwords-mysql，dev 库=演练数据来源）
    SOURCE_PASSWORD    源库密码（默认取 .env 的 DB_PASSWORD）
    TARGET_CONTAINER   演练实例容器（默认 dmkwords-mysql-prod；**必须开了 binlog**）
    TARGET_PASSWORD    演练实例密码（默认取 .env 的 DB_PASSWORD；演练实例用了别的密码时必填）
    TARGET_HOST_PORT   演练实例对宿主暴露的端口（默认 3308，冒烟用）
    TARGET_DB          演练库名（默认 dmkwords_drill；**禁与源库同名，且必须含 drill**）
    APPLY=1            真跑
    DROP_AFTER=1       演练后删掉演练库（默认保留供人工复核）
    SKIP_SMOKE=1       跳过冒烟
    SMOKE_PORT         冒烟后端端口（默认 8009）
    REPLAY_MODE        binlog 重放位置：`container`（默认，容器内 mysqlbinlog）/
                       `host`（宿主 mysqlbinlog，需装 mysql-client；官方 mysql:8.0 镜像**不含** mysqlbinlog）
    MYSQLBINLOG_BIN    宿主上 mysqlbinlog 的路径（REPLAY_MODE=host 时用，默认从 PATH 找）
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from backend.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
S = get_settings()

DUMP_FILE = os.environ.get("DUMP_FILE", "")
SOURCE_CONTAINER = os.environ.get("SOURCE_CONTAINER", "dmkwords-mysql")
TARGET_CONTAINER = os.environ.get("TARGET_CONTAINER", "dmkwords-mysql-prod")
TARGET_HOST_PORT = os.environ.get("TARGET_HOST_PORT", "3308")
TARGET_DB = os.environ.get("TARGET_DB", "dmkwords_drill")
SOURCE_PASSWORD = os.environ.get("SOURCE_PASSWORD") or S.DB_PASSWORD
TARGET_PASSWORD = os.environ.get("TARGET_PASSWORD") or S.DB_PASSWORD
APPLY = os.environ.get("APPLY") == "1"
DROP_AFTER = os.environ.get("DROP_AFTER") == "1"
SKIP_SMOKE = os.environ.get("SKIP_SMOKE") == "1"
SMOKE_PORT = os.environ.get("SMOKE_PORT", "8009")

PARITY_TABLES = ("books", "parents", "children", "orders", "activities")

_failures: list[str] = []
_blocked: list[str] = []


def blocked(msg: str) -> None:
    print(f"    ⛔ {msg}", flush=True)
    _blocked.append(msg)


def _password_for(container: str) -> str:
    return SOURCE_PASSWORD if container == SOURCE_CONTAINER else TARGET_PASSWORD


def _env_with_secret(container: str) -> dict:
    """子进程环境：MYSQL_PWD 由 docker exec 的 `-e MYSQL_PWD` 透传进容器，不落 argv。"""
    return {**os.environ, "MYSQL_PWD": _password_for(container)}


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


def verdict(ok: bool, msg: str) -> None:
    print(f"    {'✅' if ok else '❌'} {msg}", flush=True)
    if not ok:
        _failures.append(msg)


def _text(raw: bytes | str | None) -> str:
    """子进程输出转文本（GitHub 上会有非 UTF-8 字节混进来，别让解码再炸一次）。"""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw or ""


def docker_exec(container: str, args: list[str], *, stdin=None) -> subprocess.CompletedProcess:
    cmd = ["docker", "exec", "-i", "-e", "MYSQL_PWD", container, *args]
    return subprocess.run(
        cmd,
        stdin=stdin,
        capture_output=True,
        env=_env_with_secret(container),
    )


def mysql(
    sql: str, db: str | None = None, container: str | None = None, quiet: bool = False
) -> str:
    """容器内执行 SQL，返回制表符分隔、无列名的结果。"""
    args = ["mysql", "-uroot", "-N", "-B", "-e", sql]
    if db:
        args.append(db)
    proc = docker_exec(container or TARGET_CONTAINER, args)
    if proc.returncode != 0:
        if quiet:
            raise RuntimeError(_text(proc.stderr)[:300])
        print(f"✗ SQL 失败（{container or TARGET_CONTAINER}）：{_text(proc.stderr)[:300]}")
        print(f"  SQL: {sql[:300]}")
        sys.exit(1)
    return (proc.stdout or b"").decode("utf-8", errors="replace").strip()


def one(sql: str, db: str | None = None, container: str | None = None, quiet: bool = False) -> str:
    out = mysql(sql, db=db, container=container, quiet=quiet)
    return out.splitlines()[0] if out else ""


def require_safe_target() -> None:
    """自毁防线：演练库名必须含 drill 且与业务库不同名。"""
    if TARGET_DB == S.DB_NAME or "drill" not in TARGET_DB:
        print(f"✗ 演练库名 {TARGET_DB!r} 不安全（禁与业务库同名，且必须含 drill）——拒绝执行")
        sys.exit(2)


def plan(what: list[str]) -> None:
    print("dry-run（未做任何改动）。将执行：")
    for i, step in enumerate(what, 1):
        print(f"  {i}. {step}")
    print("\n真跑请加 APPLY=1。")
    sys.exit(0)


# ────────────────────────── 全量备份 / 恢复 / 对拍 ──────────────────────────


def make_dump() -> Path:
    if DUMP_FILE:
        path = Path(DUMP_FILE)
        if not path.is_file():
            print(f"✗ DUMP_FILE 不存在：{path}")
            sys.exit(2)
        log("1/6", f"用现成备份：{path}（{path.stat().st_size / 1024:.0f} KB）")
        return path
    out_dir = ROOT / "backups"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{S.DB_NAME}_full_{time.strftime('%Y%m%d_%H%M%S')}_drill.sql.gz"
    # 与 scripts/backup_db.sh 同款参数（一致性快照 + 例程/触发器/事件）。
    # **必须走 shell 管道**：python 的 `subprocess(stdout=GzipFile)` 用的是原始 fd，
    # 会绕过 gzip 层——写出"gzip 头 + 未压缩 SQL"的假备份（2026-10-08 演练实测踩到，
    # 恢复阶段必然报错）。管道写法也是 backup_db.sh 的写法。
    args = [
        "mysqldump",
        "-uroot",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--set-gtid-purged=OFF",
        "--column-statistics=0",
        S.DB_NAME,
    ]
    cmd = (
        f"docker exec -e MYSQL_PWD {SOURCE_CONTAINER} {' '.join(args)} "
        f"| gzip -c > {shlex.quote(str(path))}"
    )
    proc = subprocess.run(
        ["sh", "-c", cmd], capture_output=True, env=_env_with_secret(SOURCE_CONTAINER)
    )
    if proc.returncode != 0:
        print(f"✗ 备份失败：{_text(proc.stderr)[:400]}")
        sys.exit(1)
    size = path.stat().st_size / 1024
    log("1/6", f"全量备份完成：{path}（{size:.0f} KB，源容器 {SOURCE_CONTAINER}）")
    verdict(size > 0, "备份产物非空")
    check = subprocess.run(["gzip", "-t", str(path)], capture_output=True)
    verdict(check.returncode == 0, "备份文件 gzip 完整性校验通过")
    return path


def restore(path: Path) -> None:
    mysql(f"DROP DATABASE IF EXISTS `{TARGET_DB}`")
    mysql(f"CREATE DATABASE `{TARGET_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    # 同 make_dump：走 shell 管道（gunzip -c | docker exec -i mysql），别把 GzipFile 直接当 stdin
    cmd = (
        f"gunzip -c {shlex.quote(str(path))} "
        f"| docker exec -i -e MYSQL_PWD {TARGET_CONTAINER} mysql -uroot {TARGET_DB}"
    )
    proc = subprocess.run(
        ["sh", "-c", cmd], capture_output=True, env=_env_with_secret(TARGET_CONTAINER)
    )
    if proc.returncode != 0:
        print(f"✗ 恢复失败：{_text(proc.stderr)[:400]}")
        sys.exit(1)
    log("2/6", f"全量恢复完成 → {TARGET_CONTAINER}:{TARGET_DB}")


def compare() -> None:
    log("3/6", "五表行数对拍（源库 vs 恢复库）")
    for table in PARITY_TABLES:
        src = one(f"SELECT COUNT(*) FROM `{table}`", db=S.DB_NAME, container=SOURCE_CONTAINER)
        dst = one(f"SELECT COUNT(*) FROM `{table}`", db=TARGET_DB)
        verdict(src == dst, f"{table}: 源 {src} / 恢复 {dst}")
    src_tables = one(
        f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{S.DB_NAME}'",
        container=SOURCE_CONTAINER,
    )
    dst_tables = one(
        f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{TARGET_DB}'"
    )
    verdict(src_tables == dst_tables, f"表数: 源 {src_tables} / 恢复 {dst_tables}")
    src_ver = one(
        "SELECT version_num FROM alembic_version", db=S.DB_NAME, container=SOURCE_CONTAINER
    )
    dst_ver = one("SELECT version_num FROM alembic_version", db=TARGET_DB)
    verdict(src_ver == dst_ver and dst_ver != "", f"迁移版本: 源 {src_ver} / 恢复 {dst_ver}")


def http_ok(url: str, payload: dict | None = None) -> tuple[bool, dict]:
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(  # noqa: S310 — 固定本机地址
        url,
        data=data,
        method="POST" if data else "GET",
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return resp.status == 200, json.loads(resp.read().decode() or "{}")
    except Exception:  # noqa: BLE001 — 起服期间连接失败是常态
        return False, {}


def smoke() -> None:
    """起一个后端指向恢复库：health 200（硬判据）+ 演示账号登录（软判据，凭据不符只告警）。"""
    log("4/6", f"冒烟：起后端 :{SMOKE_PORT} 指向恢复库 {TARGET_DB}（调度器关闭）")
    env = {
        **os.environ,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": TARGET_HOST_PORT,
        "DB_NAME": TARGET_DB,
        "DB_PASSWORD": TARGET_PASSWORD,
        "SCHEDULER_ENABLED": "false",
        "APP_ENV": "dev",
        "DEBUG": "true",
        "UPLOADS_DIR": "/tmp/dmk-drill-uploads",
    }
    proc = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/uvicorn"),
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            SMOKE_PORT,
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(60):
            time.sleep(0.5)
            ok, _body = http_ok(f"http://127.0.0.1:{SMOKE_PORT}/health")
            if ok:
                break
        verdict(ok, "/health 200（恢复库可服务）")
        if ok:
            ok_login, body = http_ok(
                f"http://127.0.0.1:{SMOKE_PORT}/api/admin/login",
                {"username": "admin", "password": "dmkwords123"},
            )
            if ok_login and body.get("token"):
                log("4/6", "    演示账号登录：成功（token 已发）")
            else:
                log("4/6", "    ⚠ 演示账号登录未通过——恢复库若来自生产，请人工用真实账号登录冒烟")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ────────────────────────── binlog 点对点恢复 ──────────────────────────


def binlog_status(rotate: bool = False) -> tuple[str, int]:
    """取当前 binlog 位置（文件, 位置）。MySQL 8.4 起改用 SHOW BINARY LOG STATUS。

    `rotate=True` 先 FLUSH LOGS 换新文件（取 T0 锚点用）；**写完之后取 P1 必须 rotate=False**——
    否则 FLUSH 会把"写入"关进上一个文件、当前位置落在新文件开头，重放范围就指向空文件（实测踩到）。
    """
    if rotate:
        mysql("FLUSH LOGS")
    major_minor = one("SELECT VERSION()").split(".")[:2]
    stmt = "SHOW BINARY LOG STATUS" if major_minor >= ["8", "4"] else "SHOW MASTER STATUS"
    file_name, pos = one(stmt).split("\t")[:2]
    return file_name, int(pos)


def marker_insert(table: str, marker: str) -> int:
    """按 information_schema 生成一条最小探针行（只填 NOT NULL 且无默认值的列），返回自增 id。"""
    rows = mysql(
        "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.columns "
        f"WHERE table_schema='{TARGET_DB}' AND table_name='{table}' "
        "AND is_nullable='NO' AND column_default IS NULL AND extra NOT LIKE '%auto_increment%'"
    ).splitlines()
    names: list[str] = []
    values: list[str] = []
    for line in rows:
        name, data_type = line.split("\t")
        names.append(f"`{name}`")
        if data_type in ("int", "bigint", "smallint", "tinyint"):
            values.append("1")
        elif data_type in ("decimal", "float", "double"):
            values.append("0")
        elif data_type == "datetime":
            values.append("NOW()")
        elif data_type == "date":
            values.append("CURDATE()")
        else:
            values.append(f"'{marker}-{name}'")
    out = mysql(
        f"INSERT INTO `{TARGET_DB}`.`{table}` ({', '.join(names)}) VALUES ({', '.join(values)});"
        " SELECT LAST_INSERT_ID();"
    )
    return int(out.splitlines()[-1])


def has_mysqlbinlog(container: str) -> bool:
    """容器里有没有 mysqlbinlog。

    **官方 `mysql:8.0` 镜像没有**（只装 `mysql-community-server-minimal`，不含 mysqlbinlog）——
    2026-10-08 演练实测；宿主也没装 mysql 客户端工具时，PITR 重放这一步就无从执行。
    """
    return docker_exec(container, ["sh", "-c", "command -v mysqlbinlog"]).returncode == 0


def replay_binlog(file_name: str, p0: int, p1: int) -> bool:
    """把 binlog 段 [p0, p1) 重放进取演库。返回是否真的执行了（工具缺失 → 记受阻）。

    `REPLAY_MODE=container`（默认）：在演练容器内跑 mysqlbinlog（需要容器里有该工具）。
    `REPLAY_MODE=host`：把 binlog 拷到宿主，用宿主的 mysqlbinlog（`MYSQLBINLOG_BIN` 可指定路径）
    管道进容器 mysql——这是**部署机**的常规姿势（宿主装 mysql-client 即可）。
    """
    mode = os.environ.get("REPLAY_MODE", "container")
    if mode == "container":
        if not has_mysqlbinlog(TARGET_CONTAINER):
            blocked(
                f"binlog 重放未执行：容器 {TARGET_CONTAINER} 内没有 mysqlbinlog"
                "（官方 mysql:8.0 只装 mysql-community-server-minimal）"
            )
            print(
                "      完成 PITR 演练的两条路：① 宿主装 mysql-client 后 "
                "`REPLAY_MODE=host APPLY=1 python -m scripts.restore_drill pitr`；"
                "② 给容器加装客户端工具。",
                flush=True,
            )
            return False
        cmd = [
            "sh",
            "-c",
            f"mysqlbinlog --start-position={p0} --stop-position={p1} /var/lib/mysql/{file_name} "
            f"| mysql -uroot {TARGET_DB}",
        ]
        proc = docker_exec(TARGET_CONTAINER, cmd)
    else:
        bin_dir = ROOT / "backups" / "binlog"
        bin_dir.mkdir(parents=True, exist_ok=True)
        local = bin_dir / file_name
        copy = subprocess.run(
            ["docker", "cp", f"{TARGET_CONTAINER}:/var/lib/mysql/{file_name}", str(local)],
            capture_output=True,
            text=True,
        )
        if copy.returncode != 0:
            blocked(f"binlog 导出失败：{_text(copy.stderr)[:200]}")
            return False
        binary = os.environ.get("MYSQLBINLOG_BIN", "mysqlbinlog")
        cmd = (
            f"{binary} --start-position={p0} --stop-position={p1} {shlex.quote(str(local))} "
            f"| docker exec -i -e MYSQL_PWD {TARGET_CONTAINER} mysql -uroot {TARGET_DB}"
        )
        proc = subprocess.run(
            ["sh", "-c", cmd], capture_output=True, env=_env_with_secret(TARGET_CONTAINER)
        )
    # 管道写法只看得到末端退出码：必须同时盯 mysqlbinlog 的报错文本
    err = _text(proc.stderr)
    if proc.returncode != 0 or "command not found" in err or "ERROR" in err:
        verdict(False, f"binlog 重放失败：{err[:300]}")
        return False
    return True


def pitr(dump: Path) -> None:
    log("1/7", "准备：位置锚点 + 探针书写入")
    before = int(one(f"SELECT COUNT(*) FROM `{TARGET_DB}`.books"))
    file0, p0 = binlog_status(rotate=True)  # 全量 dump 的等效锚点：此刻库状态 = 恢复后的状态
    marker = f"PITR-DRILL-{time.strftime('%H%M%S')}"
    marker_id = marker_insert("books", marker)
    file1, p1 = binlog_status()  # 写入后的位置（**不再 FLUSH**）：恢复就重放到这里
    verdict(file0 == file1, f"锚点与写入在同一 binlog 文件（{file1}）")
    log("1/7", f"探针书 id={marker_id}（{marker}）；binlog {file1} 锚点 {p0} → 写完 {p1}")

    log("2/7", "制造误删（演练库内 DELETE 探针书）")
    mysql(f"DELETE FROM `{TARGET_DB}`.books WHERE id={marker_id}")
    verdict(
        int(one(f"SELECT COUNT(*) FROM `{TARGET_DB}`.books")) == before,
        f"误删后 books 回到 {before} 行",
    )

    log("3/7", f"制造灾难（DROP DATABASE {TARGET_DB}，仅演练库）")
    mysql(f"DROP DATABASE `{TARGET_DB}`")

    log("4/7", "恢复全量备份（回到 T0）")
    restore(dump)
    verdict(
        int(one(f"SELECT COUNT(*) FROM `{TARGET_DB}`.books")) == before,
        f"全量恢复后 books = {before} 行（探针书尚未回来）",
    )

    log("5/7", f"重放 binlog：{file1} 的 {p0} → {p1}（含探针书写入、不含误删）")
    if not replay_binlog(file1, p0, p1):
        log("5/7", "binlog 侧证据（SHOW BINLOG EVENTS，无需 mysqlbinlog）：")
        print(mysql(f"SHOW BINLOG EVENTS IN '{file1}' FROM {p0} LIMIT 10").replace("\t", "  "))
        return

    log("6/7", "断言：探针书回来了，误删没有被重放")
    verdict(
        int(one(f"SELECT COUNT(*) FROM `{TARGET_DB}`.books WHERE id={marker_id}")) == 1,
        f"探针书 id={marker_id} 已恢复",
    )
    verdict(
        int(one(f"SELECT COUNT(*) FROM `{TARGET_DB}`.books")) == before + 1,
        f"books = {before} + 1 行（恢复点 = 误删前一刻）",
    )

    log("7/7", "点对点恢复完成：全量 + binlog 重放到误删前一刻")


def drop_target() -> None:
    mysql(f"DROP DATABASE IF EXISTS `{TARGET_DB}`")
    log("清理", f"已删除演练库 {TARGET_DB}（DROP_AFTER=1）")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("full", "pitr"):
        print(__doc__)
        sys.exit(2)
    require_safe_target()

    names = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True
    ).stdout.split()
    if TARGET_CONTAINER not in names:
        print(
            f"✗ 演练实例容器 {TARGET_CONTAINER} 未运行——先 `docker compose -f "
            f"docker-compose.prod.yml up -d`（或指定 TARGET_CONTAINER）"
        )
        sys.exit(2)

    steps = {
        "full": [
            f"生成全量备份（docker exec {SOURCE_CONTAINER} mysqldump → backups/）或使用 DUMP_FILE",
            f"恢复到演练库 {TARGET_CONTAINER}:{TARGET_DB}",
            f"五表行数对拍 + 表数/迁移版本对拍（{', '.join(PARITY_TABLES)}）",
            f"冒烟：起后端 :{SMOKE_PORT} 指向恢复库 → /health + 演示账号登录",
        ],
        "pitr": [
            f"在 {TARGET_DB} 记位置锚点 → 写入探针书 → 误删 → DROP DATABASE（灾难）",
            "恢复全量备份回到 T0",
            "重放 binlog 至「写入后、误删前」的位置",
            "断言：探针书回来、误删未重放",
        ],
    }[command]
    if command == "full" and SKIP_SMOKE:
        steps[-1] += "（SKIP_SMOKE=1 已跳过）"
    if DROP_AFTER:
        steps.append(f"演练结束后删除演练库 {TARGET_DB}")
    if not APPLY:
        plan(steps)

    t0 = time.perf_counter()
    if command == "full":
        dump = make_dump()
        restore(dump)
        compare()
        if not SKIP_SMOKE:
            smoke()
    else:
        dump = Path(DUMP_FILE) if DUMP_FILE else make_dump()
        if not dump.is_file():
            print(f"✗ 需要一份全量备份作为恢复基准：DUMP_FILE={dump}")
            sys.exit(2)
        pitr(dump)

    if DROP_AFTER:
        drop_target()
    state = "通过" if not _failures and not _blocked else ("受阻" if _blocked else "未通过")
    print(f"\n=== 演练{state}（{time.perf_counter() - t0:.1f}s）===")
    for item in _failures:
        print(f"  ❌ {item}")
    for item in _blocked:
        print(f"  ⛔ {item}")
    sys.exit(2 if _blocked and not _failures else (1 if _failures else 0))


if __name__ == "__main__":
    main()
