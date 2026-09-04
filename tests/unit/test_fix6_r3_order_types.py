# tests/unit/test_fix6_r3_order_types.py — 插修 6 R3（FEAT-080）管理端订单类型扩展
"""红测试（对齐 D8 Gherkin 四场景）：

1. 押金单创建 → Order(deposit, 金额=deposit_amount 配置) + confirm 激活
   Deposit(paid) + Ledger(pay, balance_after=标准值)
2. 活动单创建 → Order(activity_fee, 金额=活动 fee)；活动不存在 → 404/422
3. 自定义单创建 → Order(custom, 自输金额) → 会员资格/到期日不变（灵魂断言）；
   缺说明/金额 → 422
4. 自定义单走退款链 → 可退全额
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay_deposit


def _mk_order(client, h, child_id, **body):
    return client.post("/api/admin/orders", json={"child_id": child_id, **body}, headers=h)


def test_r3_deposit_order_activates_deposit_module(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981036001", "押金单孩")
    r = _mk_order(client, h, c["id"], order_type="deposit")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["order_type"] == "deposit"
    assert Decimal(o["amount"]) == Decimal("1200.00"), f"押金单金额=配置 1200，实 {o['amount']}"

    # confirm → 押金模块激活（Deposit paid + Ledger pay）
    rc = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert rc.status_code == 200, rc.text
    from backend.domain.billing.models import Deposit, DepositLedger

    with _db() as db:
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"], Deposit.is_deleted == 0).first()
        assert dep is not None and dep.status == Deposit.STATUS_PAID
        assert dep.available_amount == Decimal("1200.00")
        ledger = (
            db.query(DepositLedger)
            .filter(
                DepositLedger.deposit_id == dep.id,
                DepositLedger.entry_type == DepositLedger.ENTRY_PAY,
            )
            .order_by(DepositLedger.id.desc())
            .first()
        )
        assert ledger is not None and ledger.balance_after == Decimal("1200.00")


def test_r3_activity_order_carries_fee(client: TestClient):
    from datetime import datetime, timedelta

    h = _h(client)
    c = client.post(
        "/api/admin/members/parents",
        json={"name": "活动单家长", "phone": "13981036002"},
        headers=h,
    ).json()
    child = client.post(
        f"/api/admin/members/parents/{c['id']}/children", json={"name": "活动单孩"}, headers=h
    ).json()
    act = client.post(
        "/api/admin/activities",
        json={
            "title": "R3 活动单活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(hours=72)).isoformat(),
            "location": "馆内",
            "max_quota": 10,
            "fee": 66,
        },
        headers=h,
    ).json()
    r = _mk_order(client, h, child["id"], order_type="activity_fee", activity_id=act["id"])
    assert r.status_code == 200, r.text
    assert r.json()["order_type"] == "activity_fee"
    assert Decimal(r.json()["amount"]) == Decimal("66.00")

    # 活动不存在 → 404
    r2 = _mk_order(client, h, child["id"], order_type="activity_fee", activity_id=99999)
    assert r2.status_code == 404, f"活动不存在应 404，实 {r2.status_code}"


def test_r3_custom_order_no_membership_impact(client: TestClient):
    """灵魂断言：自定义单确认收款后会员资格/到期日不变（纯资金流水）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981036003", "自定义单孩")
    _pay_deposit(client, h, c["id"])
    from backend.domain.identity.models import Child

    with _db() as db:
        before = db.query(Child).filter(Child.id == c["id"]).first()
        status_before, expire_before = before.member_status, before.member_expire

    r = _mk_order(client, h, c["id"], order_type="custom", amount="88.00", remark="春季材料费")
    assert r.status_code == 200, r.text
    assert r.json()["order_type"] == "custom"
    assert Decimal(r.json()["amount"]) == Decimal("88.00")

    rc = client.post(
        f"/api/admin/orders/{r.json()['id']}/confirm-payment",
        json={"pay_method": "cash"},
        headers=h,
    )
    assert rc.status_code == 200, rc.text

    with _db() as db:
        after = db.query(Child).filter(Child.id == c["id"]).first()
        assert after.member_status == status_before, (
            f"会员状态应不变（{status_before}），实 {after.member_status}（灵魂断言）"
        )
        assert after.member_expire == expire_before, "到期日应不变"

    # 边界：缺说明/缺金额 → 422
    assert _mk_order(client, h, c["id"], order_type="custom", amount="10").status_code == 422
    assert _mk_order(client, h, c["id"], order_type="custom", remark="只有说明").status_code == 422


def test_r3_custom_order_refund_full(client: TestClient):
    """自定义单可走退款链（可退全额）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981036004", "自定义退款孩")
    r = _mk_order(client, h, c["id"], order_type="custom", amount="50.00", remark="退款链测试")
    assert r.status_code == 200
    o = r.json()
    rc = client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert rc.status_code == 200
    ra = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": o["id"], "reason": "自定义单退款"},
        headers=mini,
    )
    assert ra.status_code == 200, (
        f"自定义单应可申请退款（可退全额），实 {ra.status_code} {ra.text[:80]}"
    )
