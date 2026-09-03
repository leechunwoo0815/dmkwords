# features/environment.py — behave 测试环境（真实 MySQL + 真实 HTTP 层）
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import engine
from backend.domain.admin.service import invalidate_config_cache
from backend.main import app

ADMIN_TABLES = ["audit_logs", "system_configs", "admin_users"]
# F7：BDD 业务数据清理（对齐 conftest clean_db 全表清单；behave 写业务数据的
# feature 自 WM3-B1 起，此前仅 admin 三表→跨场景唯一键残留 409）
BDD_BIZ_TABLES = [
    "orders",
    "children",
    "parents",
    "observation_reports",
    "borrow_records",
    "reservations",
    "deposit_ledgers",
    "deposits",
    "vocabularies",
    "favorites",
    "checkins",
    "reading_progress",
]


def before_scenario(context, scenario):
    if "draft" in scenario.effective_tags:
        return
    # I-3/T8 同族：RateLimiter 全局实例（内存）在 behave 进程内跨场景累积，
    # admin login 第 6 次即 429（gate p0batch1 首轮 BDD 11 场景失败实证）——
    # 对齐 tests/unit/conftest.py clean_db 的重置
    from backend.middleware.rate_limit import _limiter as _rate_limiter

    _rate_limiter._requests.clear()
    with engine.begin() as conn:
        for table in ADMIN_TABLES + BDD_BIZ_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
    from backend.seeds.seed_admin import seed as seed_admin
    from backend.seeds.seed_configs import seed as seed_configs

    seed_admin()
    seed_configs()
    invalidate_config_cache()
    context.client = TestClient(app)
    context.admin_token = None
    context.staff_token = None


def _login(context, username: str, password: str) -> str:
    resp = context.client.post(
        "/api/admin/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["token"]


def get_admin_headers(context) -> dict:
    if not context.admin_token:
        context.admin_token = _login(context, "admin", "dmkwords123")
    return {"Authorization": f"Bearer {context.admin_token}"}


def get_staff_headers(context) -> dict:
    if not context.staff_token:
        context.staff_token = _login(context, "staff01", "dmkwords123")
    return {"Authorization": f"Bearer {context.staff_token}"}
