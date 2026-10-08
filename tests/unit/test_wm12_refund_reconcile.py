# tests/unit/test_wm12_refund_reconcile.py — 原路退款 + 对账（WM12-B）
"""断言的是"钱退对没退对"：

① 线上单退款：执行即**自动原路退回**（mock 即时成功）→ 退款单 refunded + 订单 refunded + 单号落库
② 受理 ≠ 成功：网关回 `PROCESSING` → 退款单留在"执行中"，等退款结果通知（钱没到账不能显示已退）
③ 幂等：已终态的退款单不可重复执行；网关失败后重试换新单号（`RF{id}-2-1`）
④ 退款回调三态：SUCCESS 落已退款 / ABNORMAL 记失败 / 已退款的重复与迟到通知一律忽略（乱序守卫）
⑤ 渠道跟着原单走：线下收款单仍走人工登记（**不碰网关**）；押金多笔线上支付不自动原路退
⑥ 对账：本地四类差异能查出来；微信账单比对用**合成账单**验三类差异；缺凭据记 skipped
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.domain.identity.models import Order, RefundRequest
from backend.domain.identity.payment_models import PaymentReconciliation
from backend.domain.identity.payment_reconcile_service import PaymentReconcileService
from tests.unit.test_wm12_payment import _bind_openid, _family, _h, _online_order

# ---------- 工具 ----------


def _online_paid_order(client, db, order_type="observation_fee", phone="13800009911"):
    """建一个"线上支付成功"的订单（mock 即时到账），返回 (parent, child, mh, order_row)。"""
    parent, child, mh = _family(client, phone=phone)
    _bind_openid(db, parent["id"], openid=f"o_test_{phone}")
    created = _online_order(client, mh, child["id"], order_type)
    pay = client.post(f"/api/miniapp/orders/{created['id']}/pay", headers=mh)
    assert pay.status_code == 200, pay.text
    row = db.query(Order).filter(Order.order_no == created["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PAID and row.transaction_id
    return parent, child, mh, row


def _apply_refund(client, mh, order_row, child_id, reason="家长申请退款"):
    """家长端发起退款申请（走真实申请链）。"""
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": child_id, "order_id": order_row.id, "reason": reason},
        headers=mh,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _pending_deposit_refund(db, child_id: int, deposit_id: int, amount="1200.00") -> int:
    """直接造一张"待审核的押金退款单"（真实的押金退款单由退会结算链生成，这里只测渠道分流）。"""
    req = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=deposit_id,
        child_id=child_id,
        amount=Decimal(amount),
        reason="退押金（测试造单）",
        status=RefundRequest.STATUS_PENDING,
    )
    db.add(req)
    db.commit()
    return req.id


def _approve(client, h, request_id):
    r = client.post(
        f"/api/admin/refund-requests/{request_id}/review",
        json={"approve": True, "remark": "同意退款"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _execute(client, h, request_id, success=True, remark="退款执行"):
    r = client.post(
        f"/api/admin/refund-requests/{request_id}/execute",
        json={"success": success, "remark": remark},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _refund_row(db, request_id):
    row = db.query(RefundRequest).filter(RefundRequest.id == request_id).first()
    db.refresh(row)
    return row


def _stub_gateway(
    monkeypatch, *, success=True, state="", refund_id="wx_refund_x", error="", refund_boom=False
):
    """把支付网关换成桩：**只接管退款**，下单仍走 mock（否则"线上支付成功"那步造不出来）。"""
    from backend.common.gateways.payment.mock import MockPaymentGateway
    from backend.common.gateways.payment.types import PaymentRefundResponse
    from backend.integrations import payment as payment_pkg

    class _Stub:
        def __init__(self):
            self.calls = []
            self._mock = MockPaymentGateway()

        @property
        def supports_instant_payment(self):
            return True

        async def create_order(self, request):
            return await self._mock.create_order(request)

        async def verify_callback_signature(self, body, signature, timestamp, nonce):
            return await self._mock.verify_callback_signature(body, signature, timestamp, nonce)

        async def decrypt_callback_data(self, ciphertext, nonce, associated_data):
            return await self._mock.decrypt_callback_data(ciphertext, nonce, associated_data)

        async def refund(self, request):
            if refund_boom:
                raise AssertionError("这条路径不应调用支付网关（退款）")
            self.calls.append(request)
            return PaymentRefundResponse(
                success=success, refund_id=refund_id, state=state, error_message=error
            )

    stub = _Stub()
    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: stub)
    return stub


def _never_refund(monkeypatch):
    """断言"不调网关退款"（下单照常）。"""
    return _stub_gateway(monkeypatch, refund_boom=True)


# ---------- ① 原路退款全链 ----------


def test_online_refund_settles_via_gateway(client, db):
    """线上单：审核通过后执行 → 自动原路退回（mock 即时成功）→ 退款单与订单双双 refunded。"""
    _parent, child, mh, order = _online_paid_order(client, db)
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    result = _execute(client, h, applied["id"], remark="线上原路退")

    assert result["channel"] == "wechat"
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    assert req.out_refund_no == f"RF{req.id}-1-1"
    assert req.gateway_refund_id and req.gateway_refund_id.startswith("mock_refund_")
    assert req.gateway_attempts == 1
    db.refresh(order)
    assert order.status == Order.STATUS_REFUNDED
    assert order.refund_status == Order.REFUND_STATUS_REFUNDED


def test_offline_order_refund_does_not_touch_gateway(client, db, monkeypatch):
    """线下收款单：执行仍走人工打款登记（**不调网关**），渠道标注 offline。"""
    _never_refund(monkeypatch)
    _parent, child, mh = _family(client, phone="13800009912")
    h = _h(client)
    created = client.post(
        "/api/admin/orders",
        json={"child_id": child["id"], "order_type": "observation_fee", "remark": "到店现金"},
        headers=h,
    ).json()
    assert (
        client.post(
            f"/api/admin/orders/{created['id']}/confirm-payment",
            json={"pay_method": "cash", "remark": "现金已收"},
            headers=h,
        ).status_code
        == 200
    )
    order = db.query(Order).filter(Order.id == created["id"]).first()
    db.refresh(order)
    applied = _apply_refund(client, mh, order, child["id"])
    _approve(client, h, applied["id"])
    result = _execute(client, h, applied["id"], remark="现金退款（线下登记）")
    assert result["channel"] == "offline"
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    assert (req.out_refund_no or "") == ""


def test_gateway_processing_keeps_refund_in_progress(client, db, monkeypatch):
    """网关回 PROCESSING（微信受理但钱在路上）→ 退款单留在"执行中"，不落"已退款"。"""
    stub = _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_refund_proc")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009913")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    result = _execute(client, h, applied["id"], remark="原路退")

    assert result["status"] == RefundRequest.STATUS_PROCESSING
    assert len(stub.calls) == 1
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING
    assert req.gateway_refund_id == "wx_refund_proc"
    db.refresh(order)
    assert order.status == Order.STATUS_PAID  # 钱没到账，订单还是已支付


def test_gateway_failure_marks_refund_failed_and_retry_uses_new_number(client, db, monkeypatch):
    """网关失败 → 退款单 failed + 订单 refund_status=failed；重试换新单号（-2-1）。"""
    _stub_gateway(monkeypatch, success=False, error="余额不足")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009914")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_FAILED
    assert req.out_refund_no == f"RF{req.id}-1-1"
    db.refresh(order)
    assert order.refund_status == Order.REFUND_STATUS_FAILED

    _execute(client, h, applied["id"], remark="原路退（重试）")
    req = _refund_row(db, applied["id"])
    assert req.gateway_attempts == 2
    assert req.out_refund_no == f"RF{req.id}-2-1"


def test_settled_refund_cannot_execute_again(client, db):
    """已退款（终态）的退款单不能再次执行（幂等：不重复出款）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009915")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    again = client.post(
        f"/api/admin/refund-requests/{applied['id']}/execute",
        json={"success": True, "remark": "再来一次"},
        headers=h,
    )
    assert again.status_code == 422
    assert _refund_row(db, applied["id"]).gateway_attempts == 1  # 没有再提交网关


# ---------- ⑤ 押金渠道判定 ----------


def test_deposit_single_online_payment_refunds_via_gateway(client, db, monkeypatch):
    """押金由**单笔**线上支付构成 → 自动原路退（一笔退款对一笔原交易）。"""
    stub = _stub_gateway(monkeypatch)
    _parent, child, mh, _order = _online_paid_order(client, db, "deposit", phone="13800009916")
    from backend.domain.billing.models import Deposit

    dep = db.query(Deposit).filter(Deposit.child_id == child["id"]).first()
    assert dep is not None and dep.status == Deposit.STATUS_PAID

    req_id = _pending_deposit_refund(db, child["id"], dep.id, amount=str(dep.available_amount))
    h = _h(client)
    _approve(client, h, req_id)
    result = _execute(client, h, req_id, remark="退押金")
    assert result["channel"] == "wechat"
    assert len(stub.calls) == 1
    assert stub.calls[0].out_trade_no == _order.order_no
    assert _refund_row(db, req_id).status == RefundRequest.STATUS_REFUNDED


def test_deposit_multiple_online_payments_falls_back_to_offline(client, db, monkeypatch):
    """押金由多笔线上支付构成 → 不自动原路退（不拼单），回落线下并留痕说明。"""
    _never_refund(monkeypatch)
    parent, child, mh, first = _online_paid_order(client, db, "deposit", phone="13800009917")
    second = Order(
        order_no="DMKSUPP000001",
        order_type=Order.TYPE_DEPOSIT_SUPPLEMENT,
        parent_id=parent["id"],
        child_id=child["id"],
        amount=Decimal("300.00"),
        status=Order.STATUS_PAID,
        pay_method="wechat",
        transaction_id="txn_supp_1",
        paid_at=datetime.now(),
        remark="补缴（测试造单）",
    )
    db.add(second)
    db.commit()

    from backend.domain.billing.models import Deposit

    dep = db.query(Deposit).filter(Deposit.child_id == child["id"]).first()
    req_id = _pending_deposit_refund(db, child["id"], dep.id, amount="1200.00")
    h = _h(client)
    _approve(client, h, req_id)
    result = _execute(client, h, req_id, remark="退押金（多笔构成）")
    assert result["channel"] == "offline"
    row = _refund_row(db, req_id)
    assert row.status == RefundRequest.STATUS_REFUNDED
    assert (row.out_refund_no or "") == ""

    from backend.common.system_models import AuditLog

    db.commit()
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.action == "refund.execute", AuditLog.target_id == str(req_id))
        .all()
    )
    assert audits, "退款执行必须留审计"
    assert any("未自动原路退" in (a.detail or "") for a in audits)


# ---------- ④ 退款回调 ----------


def test_refund_notify_success_settles_request(client, db, monkeypatch):
    """退款回调 SUCCESS → 执行中的退款单落已退款 + 订单 refunded。"""
    _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_refund_1")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009918")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING

    r = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": req.out_refund_no, "refund_status": "SUCCESS"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["response"]["message"] == "已退款"
    assert _refund_row(db, applied["id"]).status == RefundRequest.STATUS_REFUNDED
    db.refresh(order)
    assert order.status == Order.STATUS_REFUNDED


def test_refund_notify_duplicate_and_late_failure_ignored(client, db):
    """已退款单：重复 SUCCESS 与迟到的 ABNORMAL 通知都被忽略（乱序守卫：钱不退两次也不回滚）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009919")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    number = req.out_refund_no

    dup = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": number, "refund_status": "SUCCESS"},
        headers=h,
    )
    assert dup.json()["response"]["message"] == "重复通知已忽略"
    late = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": number, "refund_status": "ABNORMAL"},
        headers=h,
    )
    assert late.json()["response"]["message"] == "重复通知已忽略"

    assert _refund_row(db, applied["id"]).status == RefundRequest.STATUS_REFUNDED
    db.refresh(order)
    assert order.status == Order.STATUS_REFUNDED


def test_refund_notify_abnormal_marks_failed(client, db, monkeypatch):
    """退款回调 ABNORMAL（执行中）→ 退款单记失败。"""
    _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_refund_2")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009920")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])

    r = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": req.out_refund_no, "refund_status": "ABNORMAL"},
        headers=h,
    )
    assert r.json()["response"]["message"] == "已记失败"
    assert _refund_row(db, applied["id"]).status == RefundRequest.STATUS_FAILED


def test_refund_notify_unknown_number_not_found(client):
    """回调单号不在库里 → 404 FAIL（异常要暴露给运维）。"""
    r = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": "RF999999-1-1"},
        headers=_h(client),
    )
    assert r.status_code == 404


def test_simulate_refund_callback_staff_denied(client, db):
    """退款回调演练：专员 403（超管专用）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009921")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])

    denied = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": req.out_refund_no},
        headers=_h(client, username="staff01"),
    )
    assert denied.status_code == 403


# ---------- ⑥ 对账 ----------


def test_local_audit_detects_status_conflict_and_zombie(client, db):
    """本地审计：① 订单已支付却存在已退款单 ② 僵尸单残余 ③ mock 通道下微信比对记 skipped。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009922")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    db.refresh(order)
    order.status = Order.STATUS_PAID  # 人为改回已支付 → L1 应报冲突
    db.commit()

    zombie = Order(
        order_no="DMKZOMBIE0001",
        order_type=Order.TYPE_CUSTOM,
        parent_id=order.parent_id,
        child_id=child["id"],
        amount=Decimal("10.00"),
        status=Order.STATUS_PENDING_PAYMENT,
        remark="僵尸单测试",
    )
    db.add(zombie)
    db.commit()
    db.query(Order).filter(Order.id == zombie.id).update(
        {"create_time": datetime.now() - timedelta(hours=100)}
    )
    db.commit()

    report = PaymentReconcileService(db).run(with_wechat=True, bill_date=date(2026, 10, 1))
    local = report["reports"][0]
    kinds = {d["kind"] for d in local["detail"]}
    assert "paid_order_with_refunded_request" in kinds
    assert "zombie_order" in kinds
    assert local["status"] == PaymentReconciliation.STATUS_DIFF

    wechat = report["reports"][1]
    assert wechat["status"] == PaymentReconciliation.STATUS_SKIPPED
    assert "mock" in wechat["note"]


def test_local_audit_detects_stuck_refund(client, db, monkeypatch):
    """本地审计：悬挂退款（执行中超过阈值）能被查出来。"""
    _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_refund_3")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009923")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    db.query(RefundRequest).filter(RefundRequest.id == applied["id"]).update(
        {"reviewed_at": datetime.now() - timedelta(hours=48)}
    )
    db.commit()

    _checked, diffs = PaymentReconcileService(db)._local_audit()
    assert any(d["kind"] == "stuck_refund" for d in diffs)


def test_bill_diff_detects_three_kinds(client, db):
    """微信账单比对（合成账单）：账单有我库无 / 金额不符 / 我库有账单无。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009924")
    db.refresh(order)
    order.paid_at = datetime(2026, 10, 1, 10, 0, 0)
    db.commit()

    mismatched = Order(
        order_no="DMKMISMATCH001",
        order_type=Order.TYPE_CUSTOM,
        parent_id=order.parent_id,
        child_id=child["id"],
        amount=Decimal("88.00"),
        status=Order.STATUS_PAID,
        pay_method="wechat",
        transaction_id="txn_mismatch",
        paid_at=datetime(2026, 10, 1, 11, 0, 0),
        remark="金额不符（测试造单）",
    )
    local_only = Order(
        order_no="DMKLOCALONLY01",
        order_type=Order.TYPE_CUSTOM,
        parent_id=order.parent_id,
        child_id=child["id"],
        amount=Decimal("66.00"),
        status=Order.STATUS_PAID,
        pay_method="wechat",
        transaction_id="txn_local_only",
        paid_at=datetime(2026, 10, 1, 12, 0, 0),
        remark="本地独有（测试造单）",
    )
    db.add_all([mismatched, local_only])
    db.commit()

    header = (
        "交易时间,公众账号ID,商户号,微信订单号,商户订单号,交易状态,订单金额,"
        "商户退款单号,退款金额,退款状态"
    )
    rows = [
        f"2026-10-01 10:00:00,wxapp,1900,txn_1,{order.order_no},SUCCESS,500.00,,,",
        f"2026-10-01 11:00:00,wxapp,1900,txn_2,{mismatched.order_no},SUCCESS,99.00,,,",
        "2026-10-01 13:00:00,wxapp,1900,txn_3,DMKBILLONLY01,SUCCESS,66.00,,,",
    ]
    csv_text = "\n".join([header, *rows, "总交易单数,3"])

    checked, diffs = PaymentReconcileService(db)._diff_bill(date(2026, 10, 1), csv_text)
    kinds = {d["kind"] for d in diffs}
    assert checked == 3
    assert "bill_only" in kinds  # DMKBILLONLY01：账单有、本地无
    assert "amount_mismatch" in kinds  # mismatched：账单 99 vs 本地 88
    assert "local_only" in kinds  # local_only：本地有、账单无


def test_bill_parse_rejects_broken_format():
    """账单格式变了要抛错（宁可整轮 failed，也不要静默少比几列）。"""
    with pytest.raises(ValueError):
        PaymentReconcileService._parse_bill("随便一行,没有表头")
    with pytest.raises(ValueError):
        PaymentReconcileService._parse_bill("商户订单号,交易状态\nDMK1,SUCCESS")


def test_bill_diff_refund_rows(client, db, monkeypatch):
    """账单里的退款行：微信已退但本地还卡在执行中 / 退款金额不符，都要报出来。"""
    _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_refund_4")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009925")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING
    # 账单比对按"当日已支付"取本地单，所以把支付时间对齐到账单日
    db.refresh(order)
    order.paid_at = datetime(2026, 10, 3, 10, 0, 0)
    db.commit()

    header = (
        "交易时间,公众账号ID,商户号,微信订单号,商户订单号,交易状态,订单金额,"
        "商户退款单号,退款金额,退款状态"
    )
    row_ok = (
        f"2026-10-03 10:00:00,wxapp,1900,txn_9,{order.order_no},REFUND,500.00,"
        f"{req.out_refund_no},500.00,SUCCESS"
    )
    _checked, diffs = PaymentReconcileService(db)._diff_bill(
        date(2026, 10, 3), "\n".join([header, row_ok, "总交易单数,1"])
    )
    kinds = {d["kind"] for d in diffs}
    assert "refund_state_mismatch" in kinds  # 微信已退成功，本地还在执行中

    row_bad_amount = (
        f"2026-10-03 10:00:00,wxapp,1900,txn_9,{order.order_no},REFUND,500.00,"
        f"{req.out_refund_no},1.00,PROCESSING"
    )
    _checked2, diffs2 = PaymentReconcileService(db)._diff_bill(
        date(2026, 10, 3), "\n".join([header, row_bad_amount, "总交易单数,1"])
    )
    assert any(d["kind"] == "refund_amount_mismatch" for d in diffs2)


def test_download_bill_verifies_hash_and_gunzips(monkeypatch):
    """对账单下载：SHA256 校验（被篡改即拒）+ gzip 解压 + 当日无账单报错。"""
    import gzip
    import hashlib

    from backend.common.async_utils import run_coro
    from backend.common.exceptions import PaymentError
    from backend.integrations.wechat import pay_v3

    csv_text = "商户订单号,交易状态,订单金额\nDMK1,SUCCESS,1.00\n总交易单数,1\n"
    gzipped = gzip.compress(csv_text.encode("utf-8"))

    class _Resp:
        def __init__(self, status_code, payload=None, content=b""):
            self.status_code = status_code
            self._payload = payload or {}
            self.content = content

        def json(self):
            return self._payload

    def _meta(hash_value: str) -> _Resp:
        return _Resp(
            200,
            {
                "download_url": "https://bill.example.com/x.gz",
                "hash_value": hash_value,
                "hash_type": "SHA256",
            },
        )

    class _Client:
        """假 httpx：/v3/bill/tradebill 返回下载元信息，download_url 返回 gzip 账单。"""

        hash_value = ""

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            if url.startswith("/v3/bill/tradebill"):
                if "2026-10-04" in url:
                    return _Resp(404, {"code": "NO_BILL_EXIST", "message": "账单不存在"})
                return _meta(self.hash_value)
            return _Resp(200, content=gzipped)

    gateway = object.__new__(pay_v3.WeChatPayV3)  # 跳过 __init__（不碰真密钥/证书）
    gateway.mchid = "1900"
    gateway.private_key = None
    gateway._build_auth_header = lambda method, url, body="": (
        'WECHATPAY2-SHA256-RSA2048 mchid="1900"'
    )

    import backend.integrations.wechat.pay_v3 as pay_v3_mod

    _Client.hash_value = hashlib.sha256(gzipped).hexdigest()
    monkeypatch.setattr(pay_v3_mod.httpx, "AsyncClient", _Client)
    assert run_coro(gateway.download_bill("2026-10-03")) == csv_text
    with pytest.raises(PaymentError):
        run_coro(gateway.download_bill("2026-10-04"))  # 当日无账单

    _Client.hash_value = "0" * 64  # 哈希不符 → 内容被换过，拒绝
    with pytest.raises(PaymentError):
        run_coro(gateway.download_bill("2026-10-03"))


def test_refund_channel_is_history_not_current_state(client, db):
    """退款渠道是**历史事实**：退款完成后列表仍应显示「微信原路」，不能变回「线下打款」。

    2026-10-08 冒烟实测抓到：判据里混进了 `order.status == REFUNDED` → 退完款渠道就"变回线下"，
    运营看到的与事实不符。防重复退款是退款单状态机的职责，不该由渠道判定兼职。
    """
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009926")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    rows = client.get("/api/admin/refund-requests", headers=h).json()
    row = [r for r in rows if r["id"] == applied["id"]][0]
    assert row["status"] == RefundRequest.STATUS_REFUNDED
    assert row["refund_channel"] == "wechat"
    assert row["out_refund_no"].startswith("RF")


def test_reconcile_endpoint_rbac_and_report(client):
    """对账端点：专员可读报告（dashboard.view）不可跑；超管可跑并留报告。"""
    h = _h(client)
    staff = _h(client, username="staff01")
    denied = client.post(
        "/api/admin/payments/reconcile", json={"bill_date": "2026-10-02"}, headers=staff
    )
    assert denied.status_code == 403

    run = client.post(
        "/api/admin/payments/reconcile",
        json={"bill_date": "2026-10-02", "with_wechat": False},
        headers=h,
    )
    assert run.status_code == 200, run.text
    assert run.json()["bill_date"] == "2026-10-02"

    listed = client.get("/api/admin/payments/reconciliations?limit=5", headers=staff)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert items and items[0]["bill_date"] == "2026-10-02"
    assert items[0]["source"] == PaymentReconciliation.SOURCE_LOCAL
