# tests/unit/test_prod_skeleton.py — 上线骨架固化断言（docs/09 第三十六轮任务包）
"""对 `docs/09` 上线骨架任务包 §三 的判据 1~4（判据 5/6 是造量与演练，证据在 docs/20 §七 与 gate-runs）：

- 调度器单实例（审查 P0-4）：命名锁被占 → 不注册任务；锁丢失（连接被掐断）→ 执行前重取；
  取锁链路本身失败 → 放行（单机纪律下不因锁把定时任务整体停摆）
- 生产 compose（`docs/20` §二）：127.0.0.1 绑定 + binlog 开 + 独立容器/卷 + 密码来自 `.env`（无弱口令写死）
- nginx 模板（审查 P1-5/P2-8）：**覆盖式** XFF（禁 `$proxy_add_x_forwarded_for`）+ 与 `TRUSTED_PROXY_COUNT=1` 成对
- 4 处索引（审查 P1-6）：`information_schema.STATISTICS` 实存（防误删/漏迁移回归）
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from backend.database import engine
from backend.tasks import registry

ROOT = Path(__file__).resolve().parents[2]

#: 四处索引的期望形态（与迁移 `a8c0e2f4b6d9` / 模型 `__table_args__` 一致）
EXPECTED_INDEXES = {
    "circle_posts": ("ix_circle_posts_is_deleted_created_at", ("is_deleted", "created_at")),
    "words_ledgers": ("ix_words_ledgers_created_at", ("created_at",)),
    "orders": ("ix_orders_create_time", ("create_time",)),
    "borrow_records": ("ix_borrow_records_status_due_at", ("status", "due_at")),
}


# ───────────────────────── 调度器单实例护栏 ─────────────────────────


class _OtherReplica:
    """模拟"另一个副本"：用独立连接持有命名锁（断开连接 = MySQL 自动释放）。

    注意：`Connection.close()` **只是把连接还回池**，DBAPI 会话没断、命名锁还在——
    所以这里必须 `invalidate()`（真断），否则"另一个副本"会一直持有锁。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.conn = engine.connect()
        self.got = int(self.conn.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": name}).scalar() or 0)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": self.name})
            self.conn.commit()
        finally:
            self.conn.invalidate()


@pytest.fixture
def lock_name(monkeypatch):
    """机制测试用测试专用锁名。

    真实锁名（`dmkwords:scheduler`）在跑单测时可能正被 dev 后端持有（pytest 与 dev 共用库），
    机制测试不该受它影响；真实锁名本身另有断言钉住（见 `test_lock_name_is_stable`）。
    """
    name = f"{registry.SCHEDULER_LOCK_NAME}:test"
    registry.stop_scheduler()
    monkeypatch.setattr(registry, "SCHEDULER_LOCK_NAME", name)
    try:
        yield name
    finally:
        registry.stop_scheduler()


def test_lock_name_is_stable():
    """锁名是契约：多副本/多进程靠同一个名字互斥，改名等于拆护栏。"""
    assert registry.SCHEDULER_LOCK_NAME == "dmkwords:scheduler"


def test_lock_excludes_second_holder(lock_name):
    """命名锁是会话级的：一个连接持有期间另一个连接取不到（多副本护栏的地基）。"""
    other = _OtherReplica(lock_name)
    try:
        assert other.got == 1
        assert registry._acquire_scheduler_lock() is False
        assert registry._lock_conn is None  # 取不到就别留半开连接
    finally:
        other.close()
    # 对方释放后本进程立刻可接管
    assert registry._acquire_scheduler_lock() is True
    registry.release_scheduler_lock()


def test_start_scheduler_skips_when_another_replica_holds_lock(lock_name):
    """锁被占 → 不注册任何任务（服务照常提供 API），且不留下调度器实例。"""
    other = _OtherReplica(lock_name)
    try:
        assert registry.start_scheduler() is False
        assert registry._scheduler is None
    finally:
        other.close()


def test_start_then_stop_releases_lock(lock_name):
    """启动拿到锁 → 停止必须放锁（否则下一个副本永远起不来）。"""
    assert registry.start_scheduler() is True
    assert registry._scheduler is not None
    registry.stop_scheduler()
    assert registry._lock_conn is None
    other = _OtherReplica(lock_name)
    try:
        assert other.got == 1  # 锁真的还回去了
    finally:
        other.close()


def test_lock_is_reacquired_after_connection_drop(lock_name):
    """连接被 MySQL 掐断（wait_timeout）时锁会静默消失——执行前复核必须能发现并重取。"""
    assert registry._acquire_scheduler_lock() is True
    first = registry._lock_conn
    first.invalidate()  # 模拟连接被 MySQL 掐断：会话结束 = 锁静默消失（close() 只是还池，不够）
    assert registry.ensure_scheduler_lock() is True
    assert registry._lock_conn is not first
    registry.release_scheduler_lock()


def test_lock_failure_is_fail_open(lock_name, monkeypatch):
    """连库失败 → 放行（单机纪律下不因取不到锁把定时任务整体停摆）。"""

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("db down")

    monkeypatch.setattr("backend.database.engine", _BrokenEngine())
    assert registry._acquire_scheduler_lock() is True


def test_guarded_run_skips_when_lock_held_elsewhere(lock_name, monkeypatch):
    """调度路径：锁在别的副本手里 → 本次不跑（不是失败，是让位）。"""
    calls: list[str] = []
    monkeypatch.setattr(
        registry, "run_task", lambda name, **kw: calls.append(name) or {"task": name}
    )
    other = _OtherReplica(lock_name)
    try:
        assert registry.guarded_run("overdue_mark") is None
    finally:
        other.close()
    assert calls == []
    assert registry.guarded_run("overdue_mark") == {"task": "overdue_mark"}
    assert calls == ["overdue_mark"]
    registry.release_scheduler_lock()


# ───────────────────────── 生产 compose / nginx 模板 ─────────────────────────


def test_prod_compose_uses_isolated_project_name():
    """生产 compose 必须声明独立项目名。

    2026-10-08 实测：两份 compose 的服务名都叫 `mysql`，项目名默认取目录名 →
    同目录跑 `-f docker-compose.prod.yml up` 会把 **dev 容器替换掉**（卷没动，但 dev 连不上）。
    """
    data = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    assert data.get("name") == "dmkwords-prod"
    assert data["name"] != ROOT.name  # 与 dev（项目名 = 目录名）不同


def test_prod_compose_loopback_binlog_and_env_password():
    """127.0.0.1 绑定 + binlog 开 + 独立容器/卷 + 密码来自 .env（禁弱口令写死）。"""
    path = ROOT / "docker-compose.prod.yml"
    raw = path.read_text(encoding="utf-8")
    svc = yaml.safe_load(raw)["services"]["mysql"]

    assert svc["container_name"] == "dmkwords-mysql-prod"
    assert any("mysql_data_prod" in str(v) for v in svc["volumes"])
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]

    command = " ".join(svc["command"])
    for flag in (
        "--log-bin",
        "--server-id=1",
        "--binlog-format=ROW",
        "--binlog-expire-logs-seconds",
    ):
        assert flag in command, flag

    assert "${DB_PASSWORD" in svc["environment"]["MYSQL_ROOT_PASSWORD"]  # 从 .env 注入
    assert "dmkwords_dev" not in raw  # dev 弱口令不得出现在生产 compose


def test_nginx_template_overrides_xff():
    """覆盖式 XFF（不是追加）+ 花括号配平 + 与后端 TRUSTED_PROXY_COUNT=1 成对。"""
    raw = (ROOT / "deploy" / "nginx" / "dmkwords.conf.template").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))

    assert "proxy_set_header X-Forwarded-For $remote_addr;" in body
    # 追加式会把客户端自带的假 XFF 保留下来 → 登录限流桶可被绕过（审查 P1-5/P2-8）
    assert "$proxy_add_x_forwarded_for" not in body
    assert "proxy_set_header X-Forwarded-Proto https;" in body
    assert "client_max_body_size 200m;" in body
    assert body.count("{") == body.count("}")  # 本机无 nginx 可跑 `nginx -t`，先挡结构性错误
    assert "TRUSTED_PROXY_COUNT=1" in raw  # 成对纪律写在文件头注释里（防后人只改一边）


def test_prod_start_script_binds_loopback_and_validates():
    """启动脚本必须只绑 127.0.0.1、不 --workers、并在启动前跑生产配置校验。"""
    raw = (ROOT / "deploy" / "start-backend.sh").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))
    assert "--host 127.0.0.1" in body
    assert "0.0.0.0" not in body
    assert "--workers" not in body
    assert "validate_production" in body


# ───────────────────────── 4 处索引（审查 P1-6） ─────────────────────────


@pytest.mark.parametrize("table", sorted(EXPECTED_INDEXES))
def test_prod_query_index_exists(table):
    """索引实存且列序一致——迁移漏跑/被人误删时立刻红。"""
    name, columns = EXPECTED_INDEXES[table]
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i "
                "ORDER BY SEQ_IN_INDEX"
            ),
            {"t": table, "i": name},
        ).fetchall()
    assert tuple(r[0] for r in rows) == columns
