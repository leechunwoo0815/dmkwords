# tests/unit/test_wm3_member.py — 会员与订单（真实链路）
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent(client, h, phone="13800000001", name="张女士") -> dict:
    r = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _child(client, h, parent_id: int, name="小明") -> dict:
    r = client.post(
        f"/api/admin/members/parents/{parent_id}/children", json={"name": name}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _order(client, h, child_id: int, order_type="observation_fee") -> dict:
    r = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _confirm(client, h, order_id: int, method="scan") -> dict:
    r = client.post(
        f"/api/admin/orders/{order_id}/confirm-payment",
        json={"pay_method": method, "remark": "线下扫码"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_parent_duplicate_phone_rejected(client: TestClient):
    h = _h(client)
    _parent(client, h, "13800000001")
    r = client.post(
        "/api/admin/members/parents", json={"name": "重复", "phone": "13800000001"}, headers=h
    )
    assert r.status_code == 409


def test_observation_payment_opens_membership(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000101")
    c = _child(client, h, p["id"])
    assert c["member_status"] == "none"
    o = _order(client, h, c["id"], "observation_fee")
    assert Decimal(o["amount"]) == Decimal("500")
    assert o["status"] == "pending_manual_confirm"
    _confirm(client, h, o["id"])
    r = client.get("/api/admin/members/children", headers=h)
    child = next(x for x in r.json()["items"] if x["id"] == c["id"])
    assert child["member_status"] == "observation"
    assert child["member_expire"] is not None


def test_formal_payment_direct_and_renewal(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000002")
    c = _child(client, h, p["id"], "小红")
    o = _order(client, h, c["id"], "formal_fee")
    assert Decimal(o["amount"]) == Decimal("6000")
    _confirm(client, h, o["id"])
    child = next(
        x
        for x in client.get("/api/admin/members/children", headers=h).json()["items"]
        if x["id"] == c["id"]
    )
    assert child["member_status"] == "formal"
    # 提前续费顺延：再买一年 → 到期日 +365
    first_expire = child["member_expire"]
    o2 = _order(client, h, c["id"], "formal_fee")
    _confirm(client, h, o2["id"])
    child2 = next(
        x
        for x in client.get("/api/admin/members/children", headers=h).json()["items"]
        if x["id"] == c["id"]
    )
    from datetime import date, timedelta

    d1 = date.fromisoformat(first_expire)
    d2 = date.fromisoformat(child2["member_expire"])
    assert d2 == d1 + timedelta(days=365)


def test_second_child_discount_at_order_time(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000003")
    c1 = _child(client, h, p["id"], "老大")
    _confirm(client, h, _order(client, h, c1["id"], "formal_fee")["id"])
    # 老大已是 formal → 老二年费自动 9 折
    c2 = _child(client, h, p["id"], "老二")
    o = _order(client, h, c2["id"], "formal_fee")
    assert Decimal(o["amount"]) == Decimal("5400")
    # 观察期不打折
    o2 = _order(client, h, c2["id"], "observation_fee")
    assert Decimal(o2["amount"]) == Decimal("500")


def test_first_activity_once_per_account(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000004")
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"], "first_activity_fee")["id"])
    r = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "first_activity_fee"},
        headers=h,
    )
    assert r.status_code == 409


def test_first_activity_does_not_open_membership(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000005")
    c = _child(client, h, p["id"])
    _confirm(client, h, _order(client, h, c["id"], "first_activity_fee")["id"])
    child = next(
        x
        for x in client.get("/api/admin/members/children", headers=h).json()["items"]
        if x["id"] == c["id"]
    )
    assert child["member_status"] == "none"


def test_cancel_pending_order(client: TestClient):
    h = _h(client)
    p = _parent(client, h, "13800000006")
    c = _child(client, h, p["id"])
    o = _order(client, h, c["id"], "observation_fee")
    r = client.post(f"/api/admin/orders/{o['id']}/cancel", headers=h)
    assert r.json()["status"] == "cancelled"
    # 已支付订单不可取消
    o2 = _order(client, h, c["id"], "observation_fee")
    _confirm(client, h, o2["id"])
    r2 = client.post(f"/api/admin/orders/{o2['id']}/cancel", headers=h)
    assert r2.status_code == 422


def test_member_status_matrix(client: TestClient, db):
    h = _h(client)
    p = _parent(client, h, "13800000007")
    c = _child(client, h, p["id"])
    # none → formal 合法（直接买年费）
    from backend.domain.identity.models import Child

    child = db.query(Child).filter(Child.id == c["id"]).first()
    assert child.can_transition(Child.MEMBER_FORMAL)
    # none → pending_evaluation 非法
    assert not child.can_transition(Child.MEMBER_PENDING_EVALUATION)
    # withdrawn → observation（重新入会）合法
    child.member_status = Child.MEMBER_WITHDRAWN
    assert child.can_transition(Child.MEMBER_OBSERVATION)


def test_parent_search_keyword(client: TestClient):
    """W1 家长远程搜索：keyword 按姓名/手机号模糊匹配。"""
    h = _h(client)
    _parent(client, h, "13800000008", "张女士")
    _parent(client, h, "13800000009", "李四")
    r = client.get("/api/admin/members/parents", params={"keyword": "张"}, headers=h)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["name"] == "张女士"
    assert items[0]["phone"] == "13800000008"
    r2 = client.get("/api/admin/members/parents", params={"keyword": "13800000009"}, headers=h)
    assert [p["name"] for p in r2.json()] == ["李四"]
    # 无 keyword 返回全量（分页）
    r3 = client.get("/api/admin/members/parents", headers=h)
    assert r3.status_code == 200
    assert len(r3.json()) >= 2


def test_orders_counts(client: TestClient):
    """W3 订单 counts：一次返回各状态计数（语义化键名，WM13 预留）。"""
    h = _h(client)
    p = _parent(client, h, "13800000010")
    c = _child(client, h, p["id"])
    _order(client, h, c["id"], "observation_fee")  # pending_manual_confirm
    o2 = _order(client, h, c["id"], "observation_fee")
    _confirm(client, h, o2["id"])  # paid
    r = client.get("/api/admin/orders/counts", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["pending_manual_confirm"] == 1
    assert body["paid"] == 1
    assert set(body.keys()) >= {
        "total",
        "pending_payment",
        "pending_manual_confirm",
        "paid",
        "cancelled",
        "refunded",
    }


def test_orders_order_by_whitelist(client: TestClient):
    """W7 受控后端排序：amount/created_at 白名单；非法值 422。"""
    h = _h(client)
    p = _parent(client, h, "13800000011")
    c = _child(client, h, p["id"])
    o1 = _order(client, h, c["id"], "observation_fee")  # 500 → observation
    _confirm(client, h, o1["id"])
    o2 = _order(client, h, c["id"], "formal_fee")  # 6000 → formal（观察期在会，无二孩折扣）
    _confirm(client, h, o2["id"])
    r = client.get("/api/admin/orders", params={"order_by": "amount_desc"}, headers=h)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    amounts = [float(o["amount"]) for o in items]
    assert amounts == sorted(amounts, reverse=True)
    r2 = client.get("/api/admin/orders", params={"order_by": "amount_asc"}, headers=h)
    asc = [float(o["amount"]) for o in r2.json()["items"]]
    assert asc == sorted(asc)
    # 非法值 422（白名单外暴露前端 bug，非静默回退）
    r3 = client.get("/api/admin/orders", params={"order_by": "evil"}, headers=h)
    assert r3.status_code == 422
    assert "order_by" in r3.json()["detail"].lower()
