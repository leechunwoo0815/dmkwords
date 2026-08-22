# features/environment.py — behave 测试环境（真实 MySQL + 真实 HTTP 层）
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import engine
from backend.domain.admin.service import invalidate_config_cache
from backend.main import app

ADMIN_TABLES = ["audit_logs", "system_configs", "admin_users"]


def before_scenario(context, scenario):
    if "draft" in scenario.effective_tags:
        return
    with engine.begin() as conn:
        for table in ADMIN_TABLES:
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
