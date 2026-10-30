# tests/unit/test_audit_20261030_p1_fixes.py — 大审查 P1 修复判据（资金/状态机）
"""`docs/27-全项目大审查-20261030.md` 的 5 条 P1，每条一个 RED→GREEN 判据：

1. 退会结算不得再退「已随权益转让处置」的订单（转让 + 退会 = 同一笔年费双得利）；
2. 活动费退款**执行前**必须重跑退款矩阵（申请后签到 → 已参加仍全额退）；
3. 人工确认收款（无流水号）后又收到线上支付回调 → 必须留痕（防线下+线上双收无人知）；
4. 押金退款成功时孩子已重新入会 → 账户复位为未缴（否则永久卡 refunded，四条缴款入口全拒）；
5. 管理端造押金单必须过押金账户状态闸（已缴/已扣/已退不得再造全额单）。
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.unit.test_p0b3_t30_refund_matrix_guard import _paid_enrolled, _signin
from tests.unit.test_wm10_state_machines import _family, _h, _pay, _pay_deposit
from tests.unit.test_wm12_payment import _anomaly_count, _notify
from tests.unit.test_wm12_payment import _family as _pay_family


def test_withdrawal_settlement_skips_transferred_order(client: TestClient, db):
    """P1-1：已随转让处置的年费单不得进退会结算清单；未转让的对照单必须仍在。"""
    h = _h(client)
    from backend.domain.billing.models import Deposit
    from backend.domain.identity.models import Child, Order
    from backend.domain.identity.wm10_withdrawal_service import WithdrawalService

    # 孩子 A：年费已付，随后随权益转让处置（refund_status=transferred）
    _p, ca, _m = _family(client, h, "13981031001", "转让处置孩")
    oa = _pay(client, h, ca["id"], "formal_fee")
    _pay_deposit(client, h, ca["id"])
    row = db.query(Order).filter(Order.id == oa["id"]).first()
    row.refund_status = Order.REFUND_STATUS_TRANSFERRED
    db.commit()

    child_a = db.query(Child).filter(Child.id == ca["id"]).first()
    svc = WithdrawalService(db)
    items_a = svc._settle_items(child_a)
    assert all(i.get("order_id") != oa["id"] for i in items_a), f"已转让订单仍进结算清单：{items_a}"

    # 对照：未转让的观察期单必须仍可退（证明过滤没有把整条查询打瘸）
    _p2, cb, _m2 = _family(client, h, "13981031002", "对照孩")
    ob = _pay(client, h, cb["id"], "observation_fee")
    child_b = db.query(Child).filter(Child.id == cb["id"]).first()
    items_b = WithdrawalService(db)._settle_items(child_b)
    assert any(i.get("order_id") == ob["id"] for i in items_b), f"对照单未进清单：{items_b}"
    assert db.query(Deposit).filter(Deposit.child_id == ca["id"]).first() is not None


def test_activity_refund_execute_blocked_after_checkin(client: TestClient):
    """P1-2：活动开始前申请退款 → 之后签到 → 审核通过再执行，必须 422（矩阵复核）。"""
    h = _h(client)
    c, m, _act, eid, order_id = _paid_enrolled(client, h, "13981031003", "申请后签到孩")

    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order_id, "reason": "活动前先申请"},
        headers=m,
    )
    assert r.status_code == 200, f"活动前申请应受理：{r.status_code} {r.text[:120]}"
    rid = r.json()["id"]

    _signin(client, h, eid)  # 申请之后才签到（时间窗内合法参加）

    assert (
        client.post(
            f"/api/admin/refund-requests/{rid}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    r2 = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "执行退款"},
        headers=h,
    )
    assert r2.status_code == 422, (
        f"已签到活动单执行退款应 422，实 {r2.status_code} {r2.text[:160]}（RED=执行前无矩阵复核）"
    )
    assert "签到" in r2.text or "活动" in r2.text


def test_manual_paid_order_duplicate_callback_flags_anomaly(client: TestClient, db):
    """P1-3：线下确认收款（无流水号）后收到线上支付成功回调 → 200 + 资金异常留痕。"""
    h = _h(client)
    _parent, child, mh = _pay_family(client, phone="13981031004")
    order = client.post(
        "/api/miniapp/orders",
        json={"child_id": child["id"], "order_type": "observation_fee"},
        headers=mh,
    ).json()
    assert (
        client.post(
            f"/api/admin/orders/{order['id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        ).status_code
        == 200
    )

    r = _notify(client, order["order_no"], "500.00", transaction_id="txn_after_manual")
    assert r.status_code == 200 and r.json()["code"] == "SUCCESS"
    assert _anomaly_count(db, "重复收款") >= 1, "人工收款后又收到线上回调，未留资金异常"


def test_deposit_refund_success_resets_account_for_active_member(client: TestClient, db):
    """P1-4：押金退款成功时孩子已是有效会员 → 押金账户复位为未缴（可再缴）。"""
    h = _h(client)
    _p, c, _m = _family(client, h, "13981031005", "退款后入会孩")
    _pay(client, h, c["id"], "formal_fee")  # 正式会员
    _pay_deposit(client, h, c["id"])

    from backend.domain.billing.models import Deposit
    from backend.domain.identity.models import RefundRequest
    from backend.domain.identity.wm10_service import RefundService

    dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
    assert dep is not None and dep.status == Deposit.STATUS_PAID
    # 模拟「退款在途时重新入会」：退款单已通过、押金处于 refunding
    dep.status = Deposit.STATUS_REFUNDING
    req = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=dep.id,
        child_id=c["id"],
        amount=dep.available_amount,
        reason="退押金",
    )
    req.status = RefundRequest.STATUS_APPROVED
    db.add(req)
    db.commit()
    rid = req.id

    RefundService(db).execute(
        type("A", (), {"id": 1, "display_name": "超管", "role": "superadmin"})(),
        rid,
        True,
        "执行退款",
    )
    db.expire_all()
    dep = db.query(Deposit).filter(Deposit.id == dep.id).first()
    assert dep.status == Deposit.STATUS_UNPAID, f"退款成功后在册会员押金仍停在 {dep.status}"
    assert dep.amount == Decimal("0") and dep.available_amount == Decimal("0")


def test_admin_deposit_order_rejected_when_deposit_not_unpaid(client: TestClient, db):
    """P1-5：押金已缴后再走管理端通用造单 → 422（不能等确认收款时才拒）。"""
    h = _h(client)
    _p, c, _m = _family(client, h, "13981031006", "押金重复孩")
    _pay_deposit(client, h, c["id"])

    from backend.domain.billing.models import Deposit

    dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
    assert dep is not None and dep.status == Deposit.STATUS_PAID

    r = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "deposit"}, headers=h
    )
    assert r.status_code == 422, f"押金已缴仍能造押金单：{r.status_code} {r.text[:160]}"


def test_points_atomic_increment_no_lost_update(client: TestClient, db, session_pair):
    """P2-2（并发）：两个会话基于同一旧快照各加一次积分 → 终值必须是两次之和。

    RED：`points_total += n` 的读改写会让后写者把先写者覆盖成"旧值+n"（丢更新）；
    GREEN：原子自增（points_total = points_total + n）在库内累加。
    """
    h = _h(client)
    _p, c, _m = _family(client, h, "13981031007", "积分并发孩")

    from backend.domain.growth.models import ChildGrowthState
    from backend.domain.growth.service import GrowthService

    s1, s2 = session_pair
    # 先把状态行建好并提交（否则两个会话各插一次同键，Insert Intention 会互相等锁）
    GrowthService(db).ensure_state(c["id"])
    db.commit()
    st1 = GrowthService(s1).ensure_state(c["id"])
    st2 = GrowthService(s2).ensure_state(c["id"])  # 同一旧快照（points_total=0）
    GrowthService(s1)._add_points(st1, 10, "manual_adjust", "A 加 10")
    s1.commit()
    GrowthService(s2)._add_points(st2, 5, "manual_adjust", "B 加 5")
    s2.commit()

    db.expire_all()
    row = (
        db.query(ChildGrowthState)
        .filter(ChildGrowthState.child_id == c["id"], ChildGrowthState.is_deleted == 0)
        .first()
    )
    assert row.points_total == 15, f"积分丢更新：期望 15，实得 {row.points_total}"
