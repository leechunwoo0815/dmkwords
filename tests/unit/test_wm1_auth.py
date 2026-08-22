# tests/unit/test_wm1_auth.py — 认证与 /me（真实链路）
from fastapi.testclient import TestClient


def test_login_success(client: TestClient, admin_headers: dict) -> None:
    # admin_headers fixture 内部已断言 200；此处验证响应体结构
    resp = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["token"]
    assert body["user"]["role"] == "superadmin"
    assert body["user"]["username"] == "admin"


def test_login_staff_success(client: TestClient) -> None:
    resp = client.post("/api/admin/login", json={"username": "staff01", "password": "dmkwords123"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "staff"


def test_login_wrong_password(client: TestClient) -> None:
    resp = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    assert "用户名或密码错误" in resp.json()["detail"]


def test_login_unknown_user(client: TestClient) -> None:
    resp = client.post("/api/admin/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_login_disabled_account(client: TestClient, db) -> None:
    from sqlalchemy import update

    from backend.domain.admin.models import AdminUser

    db.execute(
        update(AdminUser)
        .where(AdminUser.username == "staff01")
        .values(status=AdminUser.STATUS_DISABLED)
    )
    db.commit()
    resp = client.post("/api/admin/login", json={"username": "staff01", "password": "dmkwords123"})
    assert resp.status_code == 403
    assert "禁用" in resp.json()["detail"]


def test_me_returns_user_and_permissions(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/me", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["role"] == "superadmin"
    assert "*" in body["permissions"]


def test_me_invalid_token(client: TestClient) -> None:
    resp = client.get("/api/admin/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_me_without_token(client: TestClient) -> None:
    resp = client.get("/api/admin/me")
    assert resp.status_code == 401  # HTTPBearer 未携带凭据 → 401 未认证


def test_login_writes_audit_log(client: TestClient, admin_headers: dict) -> None:
    resp = client.get(
        "/api/admin/audit-logs", params={"action": "login", "page_size": 10}, headers=admin_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["action"] == "login"
