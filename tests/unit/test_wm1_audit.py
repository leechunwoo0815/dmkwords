# tests/unit/test_wm1_audit.py — 审计日志（只增不改）
from fastapi.testclient import TestClient


def test_audit_list_superadmin_ok(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/audit-logs", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1  # 至少有登录日志
    assert all("action" in item and "actor_name" in item for item in body["items"])


def test_audit_list_staff_forbidden(client: TestClient, staff_headers: dict) -> None:
    resp = client.get("/api/admin/audit-logs", headers=staff_headers)
    assert resp.status_code == 403


def test_audit_pagination(client: TestClient, admin_headers: dict) -> None:
    resp = client.get(
        "/api/admin/audit-logs", params={"page": 1, "page_size": 1}, headers=admin_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["page_size"] == 1
    assert body["has_next"] == (body["total"] > 1)


def test_audit_filter_by_action(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/audit-logs", params={"action": "login"}, headers=admin_headers)
    assert resp.status_code == 200
    assert all(item["action"] == "login" for item in resp.json()["items"])


def test_audit_logs_immutable(client: TestClient, admin_headers: dict) -> None:
    """日志不可篡改：不存在任何编辑/删除端点。"""
    resp_del = client.delete("/api/admin/audit-logs/1", headers=admin_headers)
    resp_put = client.put("/api/admin/audit-logs/1", json={"reason": "篡改"}, headers=admin_headers)
    assert resp_del.status_code in (404, 405)
    assert resp_put.status_code in (404, 405)
