# tests/unit/test_wm1_dashboard.py — 仪表盘运行数据（真实链路）
from fastapi.testclient import TestClient


def test_dashboard_overview_fields(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/dashboard", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["admin_count"] == 2  # admin + staff01
    assert body["config_count"] == 29
    assert body["today_logins"] >= 1  # admin_headers fixture 的登录
    assert isinstance(body["recent_config_changes"], list)


def test_dashboard_reflects_config_change(client: TestClient, admin_headers: dict) -> None:
    client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "25", "reason": "仪表盘联动验证"},
        headers=admin_headers,
    )
    resp = client.get("/api/admin/dashboard", headers=admin_headers)
    changes = resp.json()["recent_config_changes"]
    assert changes, "仪表盘应显示最近配置变更"
    latest = changes[0]
    assert latest["config_name"] == "可借上限（本）"  # 中文显示名，不是英文键
    assert latest["change"] == "30 → 25"
    assert latest["actor_name"] == "超级管理员"


def test_dashboard_staff_can_view(client: TestClient, staff_headers: dict) -> None:
    resp = client.get("/api/admin/dashboard", headers=staff_headers)
    assert resp.status_code == 200


def test_dashboard_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/admin/dashboard")
    assert resp.status_code in (401, 403)
