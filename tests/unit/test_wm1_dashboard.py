# tests/unit/test_wm1_dashboard.py — 仪表盘运行数据（真实链路）
from fastapi.testclient import TestClient


def test_dashboard_overview_fields(client: TestClient, admin_headers: dict) -> None:
    resp = client.get("/api/admin/dashboard", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["admin_count"] == 2  # admin + staff01
    assert (
        body["config_count"] == 37
    )  # 29 基础键 + ar_warning_range（C16）+ book_onboarding_check（D1）+ 6 项 WM11 提醒配置（2026-08-29）
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


def test_dashboard_business_cells(client: TestClient, admin_headers: dict) -> None:
    """C21：经营格子真实口径（R-313 会员在册 + circulation 逾期口径一致）。
    链路：缴年费+押金的正式孩子借 1 本→归还→再借→断言 8 项统计。"""
    h = admin_headers
    # 家长 + 两名孩子（formal 1、none 1 → member_total=1）
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800001501"}, headers=h
    ).json()
    c1 = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "仪表孩"}, headers=h
    ).json()
    client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "未入会孩"}, headers=h
    )
    o1 = client.post(
        "/api/admin/orders", json={"child_id": c1["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o1['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c1['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    # 书 + 副本 + 借还各一次（today_borrowed/today_returned 同日出账）
    client.post(
        "/api/admin/books",
        json={"isbn": "9780545582889", "title": "DASH", "word_count": 100},
        headers=h,
    )
    rec = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c1["id"], "isbn": "9780545582889", "override_reason": ""},
        headers=h,
    ).json()
    assert rec.get("copy_id"), rec  # 借出成功，返回 record 结构
    client.post(
        "/api/admin/circulation/return",
        json={"copy_id": rec["copy_id"], "condition": "normal"},
        headers=h,
    )
    # 再借一本（借出中 → copy_borrowed=1）
    rec2 = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c1["id"], "isbn": "9780545582889", "override_reason": ""},
        headers=h,
    )
    assert rec2.status_code == 200, rec2.text
    # 家长端真实报名活动（activity_enroll_recent ≥1）
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800001501', 'code': '1234'}).json()['token']}"
    }
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "仪表盘活动",
            "activity_type": "book_club",
            "start_at": "2099-01-01T10:00:00",
            "location": "本馆",
            "max_quota": 10,
            "fee": 0,
            "member_only": False,
            "enroll_deadline": "2099-01-01T09:00:00",
        },
        headers=h,
    )
    assert act.status_code == 200, act.text
    en = client.post(
        f"/api/miniapp/activities/{act.json()['id']}/enroll",
        json={"child_id": c1["id"]},
        headers=mini,
    )
    assert en.status_code == 200, en.text
    body = client.get("/api/admin/dashboard", headers=h).json()
    assert body["copy_total"] == 1  # 默认 1 副本
    assert body["copy_borrowed"] == 1  # 当前在借
    assert body["today_borrowed"] >= 2  # 借了两次
    assert body["today_returned"] >= 1  # 还了一次
    assert body["overdue_active"] == 0  # 刚借未到期
    assert body["member_total"] == 1  # 仅 formal 在册（none 不计）
    assert body["member_new_week"] == 2  # c1/c2 均为本周创建
    assert body["activity_enroll_recent"] == 1
