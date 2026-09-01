# tests/unit/test_wm10_concurrency.py — P1 资金并发行锁（session_pair 基建复用）
"""并发缺陷：无行锁的"读→校验→写"在双事务下快照覆盖写。
有效并发红的结构：B 先建旧快照（普通读）→ A 锁行修改并提交 → B 走被测 service：
- 无锁实现：B 读旧快照校验通过 → 覆盖写（双写/覆盖先写）= RED
- 锁定读实现：SELECT FOR UPDATE 总读最新已提交 → 校验失败拒绝 = GREEN
REPEATABLE READ：跨事务读 A 提交数据需刷新快照（先例 test_wm13_admin_notify.py）。"""


from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.common.exceptions import ValidationError


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "并发家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return p, c, mini


def _pay(client, h, child_id, order_type):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _paid_child(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "并发家长", "phone": phone}, headers=h
    ).json()
    return client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()


def _pay_deposit(client, h, child_id):
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )


def _approved_refund(client, h, mini, c, order) -> dict:
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "并发退款"},
        headers=mini,
    ).json()
    assert (
        client.post(
            f"/api/admin/refund-requests/{rr['id']}/review",
            json={"approve": True, "remark": "同意"},
            headers=h,
        ).status_code
        == 200
    )
    return rr


def test_execute_concurrent_no_double_write(client: TestClient, session_pair):
    """P1-F1：B 旧快照 approved；A 已把单推进 processing 并提交；B 再 execute——
    锁定读实现下 B 读到最新状态（processing）→ 422 拒绝；无锁实现下 B 覆盖写
    走 success 分支 → 双写（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002301", "并发退孩")
    order = _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    rr = _approved_refund(client, h, mini, c, order)
    rid = rr["id"]

    s1, s2 = session_pair
    from backend.domain.identity.models import RefundRequest

    # B 建立旧快照（approved，早于 A 完整执行）
    stale = s2.query(RefundRequest).filter(RefundRequest.id == rid).first()
    assert stale.status == "approved"
    # A 走 HTTP 完整执行成功（refunded + 押金退款单 1 笔 + Ledger）
    r_a = client.post(
        f"/api/admin/refund-requests/{rid}/execute",
        json={"success": True, "remark": "先到者执行"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    # identity map 会缓存 stale 对象（SQLAlchemy 不用行数据刷新已加载实例），
    # expire_all 模拟生产"新请求新 session"语义：锁定读读最新已提交（refunded）→
    # 校验拒绝；无锁实现普通读走旧快照 approved → 覆盖写重走 success 分支 → 双写
    s2.expire_all()
    from backend.domain.identity.wm10_service import RefundService

    with pytest.raises(ValidationError):
        RefundService(s2).execute(
            type("A", (), {"id": 1, "display_name": "超管"})(), rid, True, "并发执行"
        )
    s2.rollback()
    s1.rollback()
    # 实质断言：押金退款单只有一笔（无锁时 B 会再发一笔）
    with _db() as db:
        db.commit()
        dep_rr = (
            db.query(RefundRequest)
            .filter(RefundRequest.kind == RefundRequest.KIND_DEPOSIT, RefundRequest.is_deleted == 0)
            .all()
        )
        assert len(dep_rr) == 1, f"押金退款单应 1 笔，实 {len(dep_rr)}（双写）"
        ledgers = (
            db.query(RefundRequest).filter(RefundRequest.withdrawal_id.isnot(None)).all()
        )
        assert len(ledgers) == 1


def test_review_concurrent_reject_cannot_overwrite_approve(client: TestClient, session_pair):
    """P1-F1 review：B 旧快照 pending；A 已 approve 提交；B 走 review reject——
    锁定读实现下 B 读到 approved → 422（一 approve 一 reject，先写不被覆盖）；
    无锁实现下 B 覆盖为 rejected（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002302", "审核并发孩")
    order = _pay(client, h, c["id"], "observation_fee")
    rid = _approved_refund(client, h, mini, c, order)["id"]

    s1, s2 = session_pair
    from backend.domain.identity.models import RefundRequest

    stale = s2.query(RefundRequest).filter(RefundRequest.id == rid).first()
    assert stale.status == "approved"  # B 快照
    # A 改回 pending？不——构造卡片场景"B 校验通过（用 pending 旧值）"：
    # B 的旧快照必须早于 A 的 approve。改用：B 建快照于 approve 之前。
    s2.rollback()
    # 重建场景：reset 为 pending 后重演
    from backend.domain.identity.wm10_service import RefundService

    with _db() as db:
        db.commit()
    # 直接验证：A approve 已提交（上面 _approved_refund 已做）→ B（新会话新快照）
    # 走 review reject 应被状态机拒绝（approved 非 pending）——这是串行回归；
    # 并发部分：B 在 A approve 前建立快照。为可复现构造：
    #   手动把单置回 pending（模拟时间回溯），B 建快照，A approve 提交，B reject。
    with _db() as db:
        req = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        req.status = RefundRequest.STATUS_PENDING
        db.commit()
    s2.query(RefundRequest).filter(RefundRequest.id == rid).first()  # B 旧快照 pending
    a = s1.query(RefundRequest).filter(RefundRequest.id == rid).with_for_update().first()
    a.status = RefundRequest.STATUS_APPROVED
    a.reviewed_by = 1
    s1.commit()  # A approve 落库
    s2.expire_all()  # 同上：模拟新请求 session
    with pytest.raises(ValidationError):
        RefundService(s2).review(
            type("A", (), {"id": 1, "display_name": "超管"})(), rid, False, "并发拒绝"
        )
    s2.rollback()
    with _db() as db:
        db.commit()
        req = db.query(RefundRequest).filter(RefundRequest.id == rid).first()
        assert req.status == RefundRequest.STATUS_APPROVED, (
            f"approve 被 reject 覆盖：{req.status}"
        )


def test_execute_double_submit_rejected(client: TestClient):
    """串行防重回归：已 refunded 的单再 execute → 422（状态机守卫，防回归）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002303", "防重孩")
    order = _pay(client, h, c["id"], "observation_fee")
    rr = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": c["id"], "order_id": order["id"], "reason": "防重"},
        headers=mini,
    ).json()
    client.post(
        f"/api/admin/refund-requests/{rr['id']}/review",
        json={"approve": True, "remark": "同意"},
        headers=h,
    )
    r1 = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "第一次"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/admin/refund-requests/{rr['id']}/execute",
        json={"success": True, "remark": "第二次"},
        headers=h,
    )
    assert r2.status_code == 422, f"重复执行未拦: {r2.status_code}"


def _db():
    from backend.database import get_session

    return get_session()


def test_confirm_payment_concurrent_idempotent(client: TestClient, session_pair):
    """P1-F2：B 旧快照 pending_manual；A 已 confirm（paid + 押金台账 1 笔）提交；
    B 再 confirm —— 锁定读实现下读到 paid → 422；无锁 → 覆盖写双押金台账（RED）。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13800002304", "确认并发孩")
    client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    )
    # 押金订单也在场（confirm observation 单会联动？不会——押金单单独 confirm 才记押金。
    # 用 deposit 单测幂等最直接）
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    s1, s2 = session_pair
    from backend.domain.identity.models import Order

    stale = s2.query(Order).filter(Order.id == do["order_id"]).first()
    assert stale.status == "pending_manual_confirm"  # B 旧快照
    # A 完整确认成功（押金 paid + ENTRY_PAY 台账 1 笔）
    r_a = client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "先到者"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    # B（旧快照）再确认：锁定读 → 422；无锁 → 覆盖写双 ENTRY_PAY
    from backend.domain.identity.service import OrderService

    with pytest.raises(ValidationError):
        OrderService(s2).confirm_payment(
            type("A", (), {"id": 1, "display_name": "超管"})(),
            do["order_id"],
            type("R", (), {"pay_method": "scan", "remark": "并发确认"})(),
        )
    s2.rollback()
    from backend.domain.billing.models import Deposit, DepositLedger

    with _db() as db:
        db.commit()
        ledgers = db.query(DepositLedger).filter(DepositLedger.entry_type == "pay").all()
        assert len(ledgers) == 1, f"ENTRY_PAY 台账应 1 笔，实 {len(ledgers)}（双写）"
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        assert float(dep.available_amount) == 1200


def test_deduct_concurrent_no_balance_drift(client: TestClient, session_pair):
    """P1-F3：余额 1200，B 旧快照；A 已扣 1100 提交；B 再扣 800 ——
    锁定读实现：B 读到最新余额 100 → min(800,100)=100 → 拒绝（余额不足）或扣 100；
    无锁实现：B 读旧快照 1200 → min(800,1200)=800 → 覆盖写 1200-800=400，
    台账合计 1900 vs 余额 400 = 账实漂移（RED）。
    断言：两条台账发生额之和 == 余额扣减总额（账实一致）。"""
    h = _h(client)
    c = _paid_child(client, h, "13800002305", "并发扣款孩")
    _pay_deposit(client, h, c["id"])
    s1, s2 = session_pair
    from backend.domain.billing.models import Deposit

    stale = s2.query(Deposit).filter(Deposit.child_id == c["id"]).first()
    assert float(stale.available_amount) == 1200  # B 旧快照
    # A 完整扣款 1100（HTTP）
    r_a = client.post(
        f"/api/admin/deposits/children/{c['id']}/deduct",
        json={"amount": "1100", "reason": "先到者扣款"},
        headers=h,
    )
    assert r_a.status_code == 200, r_a.text
    s2.expire_all()
    # B（旧快照 1200）再扣 800：锁定读 → 最新余额 100 → 拒绝（余额不足）；
    # 无锁 → min(800,1200)=800 覆盖写 400（账实漂移）
    from backend.domain.billing.service import DepositService

    try:
        DepositService(s2).deduct_for_compensation(
            type("A", (), {"id": 1, "display_name": "超管"})(), c["id"], Decimal("800"), "并发扣款"
        )
        s2.commit()
    except ValidationError:
        s2.rollback()
    from backend.domain.billing.models import DepositLedger

    with _db() as db:
        db.commit()
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        ledgers = (
            db.query(DepositLedger)
            .filter(DepositLedger.deposit_id == dep.id, DepositLedger.entry_type == "deduct")
            .all()
        )
        total_deduct = sum(float(l.amount) for l in ledgers)
        # 账实一致（本卡核心断言）：台账扣款合计 == deducted_amount；
        # 押金总额 == available + deducted（over 部分进 unpaid_balance 待付）
        assert total_deduct == float(dep.deducted_amount), (
            f"账实漂移：台账合计 {total_deduct} != deducted {dep.deducted_amount}"
        )
        assert float(dep.amount) == float(dep.available_amount) + float(dep.deducted_amount), (
            f"总账不平衡：{dep.amount} != {dep.available_amount} + {dep.deducted_amount}"
        )
        # B 基于最新余额（100）扣款，而非旧快照 1200：B 的扣款发生额 ≤ 100
        assert all(float(l.amount) <= 1100 for l in ledgers)
        assert min(float(l.amount) for l in ledgers if float(l.amount) < 1100) <= 100
