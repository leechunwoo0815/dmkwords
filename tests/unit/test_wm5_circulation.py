# tests/unit/test_wm5_circulation.py — 借阅操作台（真实链路）
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _full_child(
    client, h, phone="13800000501", name="借书孩", isbn="9780545582889"
) -> tuple[dict, dict]:
    """观察期会员 + 押金已缴 + 一本在馆书。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    book = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": "Dog Man", "word_count": 2500}, headers=h
    ).json()
    return c, book


def test_child_card_and_borrow(client: TestClient):
    h = _h(client)
    c, book = _full_child(client, h)
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["member_status"] == "observation"
    assert card["available_quota"] == 30
    assert card["deposit_status"] == "paid"
    # 借书
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r.status_code == 200, r.text
    record = r.json()
    assert record["status"] == "active"
    # 卡片更新
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 1
    assert card["available_quota"] == 29


def test_borrow_same_book_twice_rejected(client: TestClient):
    h = _h(client)
    c, book = _full_child(client, h, "13800000502", "重复孩")
    client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r.status_code == 409
    assert "重复借阅" in r.json()["detail"]


def test_borrow_without_member_rejected(client: TestClient):
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800000503"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "未入会"}, headers=h
    ).json()
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9781111111111", "title": "X", "word_count": 100},
        headers=h,
    ).json()
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r.status_code == 422
    assert "未入会" in r.json()["detail"]


def test_borrow_without_deposit_needs_override(client: TestClient):
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800000504"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "无押金"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9782222222222", "title": "Y", "word_count": 100},
        headers=h,
    ).json()
    # 无放行 → 拒
    r = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r.status_code == 422
    # 放行 + 原因 → 借出 + 留痕
    r2 = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "isbn": book["isbn"],
            "override_reason": "家长明日补缴押金",
        },
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["override_reason"] == "家长明日补缴押金"


def test_return_and_renew(client: TestClient):
    h = _h(client)
    c, book = _full_child(client, h, "13800000505", "还书孩")
    record = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    ).json()
    # 续借：due +7
    r = client.post("/api/admin/circulation/renew", json={"record_id": record["id"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["renew_used"] == 1
    from datetime import datetime as dt

    d1 = dt.fromisoformat(record["due_at"])
    d2 = dt.fromisoformat(r.json()["due_at"])
    assert (d2.date() - d1.date()).days == 7
    # 再续 → 拒（1 次上限）
    r2 = client.post("/api/admin/circulation/renew", json={"record_id": record["id"]}, headers=h)
    assert r2.status_code == 422
    # 还书（正常）
    r3 = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": record["copy_id"], "condition": "normal"},
        headers=h,
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "returned"
    # 副本回到在馆，可再借
    r4 = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    )
    assert r4.status_code == 200


def test_return_lost_condition(client: TestClient):
    h = _h(client)
    c, book = _full_child(client, h, "13800000506", "遗失孩")
    record = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    ).json()
    r = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": record["copy_id"], "condition": "lost"},
        headers=h,
    )
    assert r.json()["status"] == "lost"
    # 副本变 lost；可联动押金赔偿（WM4 的 deduct）
    r2 = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "68", "reason": f"遗失《{book['title']}》", "copy_id": record["copy_id"]},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["available_amount"] == "1132.00"


def test_overdue_deduction_and_list(client: TestClient, db):
    h = _h(client)
    c, book = _full_child(client, h, "13800000507", "逾期孩")
    record = client.post(
        "/api/admin/circulation/borrow", json={"child_id": c["id"], "isbn": book["isbn"]}, headers=h
    ).json()
    # 造逾期：改到期日为昨天
    from backend.domain.circulation.models import BorrowRecord

    rec = db.query(BorrowRecord).filter(BorrowRecord.id == record["id"]).first()
    rec.due_at = datetime.now() - timedelta(days=3)
    db.commit()
    # 逾期列表出现
    overdue = client.get("/api/admin/circulation/overdue", headers=h).json()
    assert any(o["record_id"] == record["id"] for o in overdue)
    # 可借额度 = 30 - 1(逾期) - 1(在借) = 28
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["overdue_count"] == 1
    assert card["available_quota"] == 28
    # 逾期书续借拒绝
    r = client.post("/api/admin/circulation/renew", json={"record_id": record["id"]}, headers=h)
    assert r.status_code == 422
    assert "逾期" in r.json()["detail"]
