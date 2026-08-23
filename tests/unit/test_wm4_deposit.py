# tests/unit/test_wm4_deposit.py — 押金与赔偿（真实链路）
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _paid_child(client, h, phone="13800000301", name="押金孩") -> dict:
    """建家长+孩子+观察期收款，返回孩子。"""
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
    return c


def _pay_deposit(client, h, child_id: int):
    r = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h)
    assert r.status_code == 200, r.text
    order = r.json()
    r2 = client.post(
        f"/api/admin/orders/{order['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    return order


def test_deposit_pay_flow(client: TestClient):
    h = _h(client)
    c = _paid_child(client, h)
    assert client.get(f"/api/admin/deposits/children/{c['id']}", headers=h).json() is None  # 未缴
    order = _pay_deposit(client, h, c["id"])
    assert Decimal(order["amount"]) == Decimal("1200")
    dep = client.get(f"/api/admin/deposits/children/{c['id']}", headers=h).json()
    assert dep["status"] == "paid"
    assert dep["available_amount"] == "1200.00"
    # 重复缴拒绝
    r = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h)
    assert r.status_code == 422


def test_miniapp_deposit_with_ledger(client: TestClient):
    """C23：家长端押金页端点（R-312/313 守卫 + 流水 ledger 最近 20 条）。"""
    h = _h(client)
    c = _paid_child(client, h, phone="13800000410")
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000410', 'code': '1234'}).json()['token']}"
    }
    _pay_deposit(client, h, c["id"])
    body = client.get("/api/miniapp/deposits", params={"child_id": c["id"]}, headers=mini).json()
    assert body["status"] == "paid"
    assert float(body["available_amount"]) == 1200
    assert len(body["ledger"]) >= 1
    assert body["ledger"][0]["entry_type"] in ("pay", "deduct", "supplement", "refund")
    # 未缴孩子：unpaid + 空流水
    c2 = _paid_child(client, h, phone="13800000411")
    mini2 = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000411', 'code': '1234'}).json()['token']}"
    }
    body2 = client.get("/api/miniapp/deposits", params={"child_id": c2["id"]}, headers=mini2).json()
    assert body2["status"] == "unpaid"
    assert body2["ledger"] == []


def test_deduct_and_supplement(client: TestClient):
    h = _h(client)
    c = _paid_child(client, h, "13800000302", "赔偿孩")
    _pay_deposit(client, h, c["id"])
    # 赔偿 68 元
    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "68", "reason": "遗失《Dog Man》按原价赔偿"},
        headers=h,
    )
    assert r.status_code == 200
    dep = r.json()
    assert dep["available_amount"] == "1132.00"
    assert dep["status"] == "partially_deducted"
    # 补缴至全额
    r = client.post(f"/api/admin/deposits/children/{c['id']}/supplement-orders", headers=h)
    assert r.status_code == 200
    assert Decimal(r.json()["amount"]) == Decimal("68")
    client.post(
        f"/api/admin/orders/{r.json()['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    dep = client.get(f"/api/admin/deposits/children/{c['id']}", headers=h).json()
    assert dep["status"] == "paid"
    assert dep["available_amount"] == "1200.00"
    assert dep["supplemented_total"] == "68.00"
    # 流水可查
    ledgers = client.get(f"/api/admin/deposits/children/{c['id']}/ledgers", headers=h).json()
    types = [entry["entry_type"] for entry in ledgers]
    assert "pay" in types and "deduct" in types and "supplement" in types


def test_deduct_insufficient_creates_unpaid(client: TestClient):
    h = _h(client)
    c = _paid_child(client, h, "13800000303", "超扣孩")
    _pay_deposit(client, h, c["id"])
    # 扣 2000：扣到 0，差 800 待结清
    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "2000", "reason": "高价书赔偿"},
        headers=h,
    )
    dep = r.json()
    assert dep["available_amount"] == "0.00"
    assert dep["status"] == "fully_deducted"
    assert Decimal(dep["unpaid_balance"]) == Decimal("800.00")
    # 待结清时补缴订单 = 1200（补至全额）
    r2 = client.post(f"/api/admin/deposits/children/{c['id']}/supplement-orders", headers=h)
    assert Decimal(r2.json()["amount"]) == Decimal("1200")


def test_deduct_without_deposit_rejected(client: TestClient):
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800000304"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "未缴孩"}, headers=h
    ).json()
    r = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "50", "reason": "x"},
        headers=h,
    )
    assert r.status_code == 422


def test_sibling_deposits_independent(client: TestClient):
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "二孩家长", "phone": "13800000305"}, headers=h
    ).json()
    c1 = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "大宝"}, headers=h
    ).json()
    c2 = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "二宝"}, headers=h
    ).json()
    _pay_deposit(client, h, c1["id"])
    _pay_deposit(client, h, c2["id"])
    client.post(
        f"/api/admin/deposits/children/{c1['id']}/deduct",
        json={"amount": "100", "reason": "大宝赔偿"},
        headers=h,
    )
    dep2 = client.get(f"/api/admin/deposits/children/{c2['id']}", headers=h).json()
    assert dep2["available_amount"] == "1200.00"  # 二宝不受影响
