# tests/unit/test_p0_money_20261009.py — 上线前资金面 P0 修复回归（2026-10-09）
"""四条 P0（外部专家独立复核 + 本地亲验成立）的修复回归。每条都钉住"缺陷语义"：

① 99 元「每账号一次」：资格复查收进**结算单链路**（`_settle_paid`）——原先只有
   "发起支付前"与"管理端确认收款"各查一次，线上回调链（`_settle_online`）完全不查，
   管理端造单 × 线上回调即双收（实测 parent paid-99 计数=2）。
② 押金不得走订单退款通道：`OrderService.refund_order` 原先无 `order_type` 闸，押金单被
   退成 refunded 而 `Deposit` 账户纹丝不动（余额可再退一次，对账 diff_count=0）；
   同订单还可重复建在途申请（两笔全额都能执行）。
③ 押金退款执行前余额复核：两笔全额押金退款单先后执行可把 `available_amount` 打成 -1200；
   转让前置条件原先只数 PENDING，看不见已 APPROVED 的押金退款单（实测 check ok=True）。
④ 退款网关 5xx/429 = **结果未知**（抛异常 → 未知态 → 只能查单），4xx 才是确定被拒；
   账单按"退款单累计出款"对账（同一退款单换号重提两笔各自都能通过逐号校验）。

另附两个拍板项：小程序退款页提交 handler（用户拍板 A）、生产关闭 API 文档端点（拍板 a）。
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from backend.domain.billing.models import Deposit
from backend.domain.identity.models import Child, Order, Parent
from backend.domain.identity.payment_reconcile_service import PaymentReconcileService
from tests.unit.test_wm12_payment import _family, _h
from tests.unit.test_wm12_refund_reconcile import (
    _apply_refund,
    _approve,
    _execute,
    _online_paid_order,
    _pending_deposit_refund,
    _refund_row,
)

ROOT = Path(__file__).resolve().parents[2]


def _admin_order(client, h, child_id: int, order_type="first_activity_fee", remark="P0 造单"):
    r = client.post(
        "/api/admin/orders",
        json={"order_type": order_type, "child_id": child_id, "remark": remark},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _paid_count(db, parent_id: int, order_type: str) -> int:
    return (
        db.query(func.count(Order.id))
        .filter(
            Order.parent_id == parent_id,
            Order.order_type == order_type,
            Order.status == Order.STATUS_PAID,
            Order.is_deleted == 0,
        )
        .scalar()
    )


# ---------- ① 99 元资格：结算单链路复查 ----------


def test_first_activity_online_callback_blocks_second_settlement(client, db):
    """管理端造两单 × 线上回调：第二笔**不再入账**（200 + 已记录待人工处理 + anomaly）。"""
    h = _h(client)
    parent, child, _mh = _family(client, phone="13800009931")
    o1 = _admin_order(client, h, child["id"], remark="P0-A 造单#1")
    o2 = _admin_order(client, h, child["id"], remark="P0-A 造单#2")

    r1 = client.post(
        "/api/admin/payments/simulate-callback", json={"order_no": o1["order_no"]}, headers=h
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["response"]["message"] == "成功"

    r2 = client.post(
        "/api/admin/payments/simulate-callback", json={"order_no": o2["order_no"]}, headers=h
    )
    assert r2.status_code == 200, r2.text
    # 钱收了但不能入账：明确应答 + 留痕（而不是 4xx 让微信重发 24 小时）
    assert r2.json()["response"]["message"] == "已记录待人工处理"

    row2 = db.query(Order).filter(Order.order_no == o2["order_no"]).first()
    db.refresh(row2)
    assert row2.status != Order.STATUS_PAID
    assert _paid_count(db, parent["id"], Order.TYPE_FIRST_ACTIVITY) == 1

    from backend.domain.admin.models import AuditLog

    anomalies = (
        db.query(func.count(AuditLog.id))
        .filter(AuditLog.action == "payment.anomaly", AuditLog.target_id == o2["order_no"])
        .scalar()
    )
    assert anomalies >= 1, "第二笔重复入账必须留 anomaly 审计"


def test_first_activity_confirm_payment_blocks_second(client, db):
    """管理端确认收款入口：第二笔 409（同一账号只允许一笔已支付首场单）。"""
    h = _h(client)
    parent, child, _mh = _family(client, phone="13800009932")
    o1 = _admin_order(client, h, child["id"], remark="P0-A 确认#1")
    o2 = _admin_order(client, h, child["id"], remark="P0-A 确认#2")

    ok = client.post(
        f"/api/admin/orders/{o1['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "人工收款"},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    dup = client.post(
        f"/api/admin/orders/{o2['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "人工收款"},
        headers=h,
    )
    assert dup.status_code == 409, dup.text
    assert _paid_count(db, parent["id"], Order.TYPE_FIRST_ACTIVITY) == 1


# ---------- ② 押金不得走订单退款通道 + 在途申请闸 ----------


def test_deposit_order_cannot_refund_via_order_channel(client, db):
    """押金单走 `POST /admin/orders/{id}/refund` → 422（与家长端同口径）。"""
    h = _h(client)
    _parent, _child, _mh, order = _online_paid_order(client, db, "deposit", phone="13800009933")
    r = client.post(f"/api/admin/orders/{order.id}/refund", json={"remark": "押金绕道"}, headers=h)
    assert r.status_code == 422, r.text
    assert "押金" in r.json()["detail"]
    db.refresh(order)
    assert order.status == Order.STATUS_PAID, "被拒的申请不得改动订单状态"


def test_duplicate_refund_application_on_same_order_rejected(client, db):
    """同一订单的第二笔在途申请 → 409（与家长端 apply 同口径）。"""
    h = _h(client)
    _parent, _child, _mh, order = _online_paid_order(
        client, db, "observation_fee", phone="13800009934"
    )
    first = client.post(
        f"/api/admin/orders/{order.id}/refund", json={"remark": "第一次申请"}, headers=h
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/admin/orders/{order.id}/refund", json={"remark": "第二次申请"}, headers=h
    )
    assert second.status_code == 409, second.text


# ---------- ③ 押金退款执行前余额复核 + 转让前置条件认 APPROVED ----------


def test_deposit_refund_cannot_go_negative(client, db):
    """两笔全额押金退款单：第二笔执行被拒（余额不足），余额停在 0 而不是 -1200。"""
    h = _h(client)
    _parent, child, _mh, _order = _online_paid_order(client, db, "deposit", phone="13800009935")
    dep = db.query(Deposit).filter(Deposit.child_id == child["id"]).first()
    full = str(dep.available_amount)
    rr1 = _pending_deposit_refund(db, child["id"], dep.id, full)
    rr2 = _pending_deposit_refund(db, child["id"], dep.id, full)
    _approve(client, h, rr1)
    _approve(client, h, rr2)

    _execute(client, h, rr1, remark="第一笔押金退款")
    db.refresh(dep)
    assert dep.available_amount == Decimal("0.00")

    blocked = client.post(
        f"/api/admin/refund-requests/{rr2}/execute",
        json={"success": True, "remark": "第二笔押金退款"},
        headers=h,
    )
    assert blocked.status_code == 422, blocked.text
    assert "余额不足" in blocked.json()["detail"]
    db.refresh(dep)
    assert dep.available_amount == Decimal("0.00"), "终态永远不许为负"
    assert _refund_row(db, rr2).status == "approved", "校验不过不流转状态（人工处理后仍可执行）"


def test_transfer_conditions_see_approved_deposit_refund(client, db):
    """转让前置条件必须把**已批准未执行**的押金退款单算作"进行中申请"。"""
    h = _h(client)
    parent, child, _mh, _order = _online_paid_order(client, db, "deposit", phone="13800009936")
    dep = db.query(Deposit).filter(Deposit.child_id == child["id"]).first()
    rr = _pending_deposit_refund(db, child["id"], dep.id, "100.00")
    _approve(client, h, rr)

    sibling = client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "受让孩"},
        headers=h,
    )
    assert sibling.status_code == 200, sibling.text

    from backend.domain.identity.transfer_service import TransferService

    p = db.query(Parent).filter(Parent.id == parent["id"]).first()
    src = db.query(Child).filter(Child.id == child["id"]).first()
    tgt = db.query(Child).filter(Child.id == sibling.json()["id"]).first()
    checks = TransferService(db).check_conditions(p, src, tgt)
    row = next(c for c in checks if c["name"] == "转出方没有进行中的申请（退款/退会/转让）")
    assert row["ok"] is False, "APPROVED 态的押金退款单必须算进行中"


# ---------- ④ 网关 5xx = 结果未知；账单按退款单聚合 ----------


def test_pay_v3_refund_status_semantics(monkeypatch):
    """pay_v3 退款应答语义三分：200/202 受理、4xx 确定被拒、5xx/429 **结果未知**（抛异常）。"""
    import httpx

    from backend.common.exceptions import PaymentError
    from backend.common.gateways.payment.types import PaymentRefundRequest
    from backend.integrations.wechat import pay_v3

    class _Resp:
        def __init__(self, code, payload=None, text=""):
            self.status_code = code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return self._resp

    def _gateway(resp):
        gw = pay_v3.WeChatPayV3.__new__(pay_v3.WeChatPayV3)  # 免凭据实例化：只测应答映射
        gw._build_auth_header = lambda *a, **k: "mock-auth"
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client(resp))
        return gw

    request = PaymentRefundRequest(
        out_trade_no="DMK-P0-1",
        refund_amount=Decimal("500"),
        total_amount=Decimal("500"),
        out_refund_no="RF1-1-1",
        notify_url="",
    )

    with pytest.raises(PaymentError):
        asyncio.run(_gateway(_Resp(500, {"message": "系统繁忙"})).refund(request))
    with pytest.raises(PaymentError):
        asyncio.run(_gateway(_Resp(429, {"message": "频率限制"})).refund(request))

    definite = asyncio.run(_gateway(_Resp(400, {"message": "参数错误"})).refund(request))
    assert definite.success is False and "参数错误" in definite.error_message

    accepted = asyncio.run(
        _gateway(_Resp(200, {"refund_id": "wx_1", "status": "SUCCESS"})).refund(request)
    )
    assert accepted.success is True and accepted.state == "SUCCESS"


def test_bill_diff_flags_multi_number_payout(client, db):
    """同一退款单在账单里出现两个商户退款单号各出一笔 → 报 `refund_overpaid_channel`。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009937")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])

    header = (
        "交易时间,公众账号ID,商户号,微信订单号,商户订单号,交易状态,订单金额,"
        "商户退款单号,退款金额,退款状态"
    )
    rows = [
        f"2026-10-04 10:00:00,wxapp,1900,txn_a,{order.order_no},REFUND,500.00,"
        f"RF{req.id}-1-1,500.00,SUCCESS",
        f"2026-10-04 10:05:00,wxapp,1900,txn_a,{order.order_no},REFUND,500.00,"
        f"RF{req.id}-2-1,500.00,SUCCESS",
    ]
    csv_text = "\n".join([header, *rows, "总交易单数,2"])

    checked, diffs = PaymentReconcileService(db)._diff_bill(date(2026, 10, 4), csv_text)
    kinds = {d["kind"] for d in diffs}
    assert checked == 2
    assert "refund_overpaid_channel" in kinds, diffs
    over = next(d for d in diffs if d["kind"] == "refund_overpaid_channel")
    assert "累计出款" in over["message"]


# ---------- 拍板项① 小程序退款提交 handler（用户裁定 A） ----------


def test_miniapp_refund_apply_submit_wired():
    """退款页的提交按钮必须有真 handler（P0-2：wxml 有 bindtap，js 里从无 onSubmit）。"""
    js = (ROOT / "miniapp/pages/order-pkg/refund-apply/refund-apply.js").read_text(encoding="utf-8")
    wxml = (ROOT / "miniapp/pages/order-pkg/refund-apply/refund-apply.wxml").read_text(
        encoding="utf-8"
    )
    assert 'bindtap="onSubmit"' in wxml
    assert "async onSubmit()" in js
    assert "api.applyRefund(" in js, "提交必须走既有 api 封装（后端端点早已就绪）"


# ---------- 拍板项② 生产关闭 API 文档端点（用户裁定 a） ----------


def test_docs_endpoints_closed_in_production(client):
    from backend.main import docs_endpoint_urls

    prod = docs_endpoint_urls("production")
    assert prod == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert docs_endpoint_urls(" Production ")["docs_url"] is None  # 大小写/空白不敏感
    dev = docs_endpoint_urls("dev")
    assert dev["docs_url"] == "/docs" and dev["openapi_url"] == "/openapi.json"
    # 测试/开发态保持开启（联调与前端类型生成不受影响）
    assert client.get("/docs").status_code == 200
