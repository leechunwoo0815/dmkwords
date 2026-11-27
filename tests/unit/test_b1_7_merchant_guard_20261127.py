# tests/unit/test_b1_7_merchant_guard_20261127.py — B1-7 回调商户归属校验（第一百八十九批）
"""双审查 P2：v3 回调的签名只证明"报文由微信签的"，**不证明这笔钱是我们的**。

修复前：解密后直接按 `out_trade_no` / `out_refund_no` 找单，完全不看明文里的
`mchid` / `appid`。别人的商户回调打到本地址（配置串了、或换商户号后旧商户回调还在路上）
只要单号与金额碰巧对得上，就会**照常入账 / 把退款项落终态**。

修复后：解密完成立刻校验归属（`PaymentNotifyMixin._merchant_guard`），支付与退款两条路径共用：
  · 配置有值 且 回调有值 → 必须逐字相等，不等 → **400 + 资金异常待办**，不入账、不落终态；
  · 任一侧为空 → 跳过该项（退款通知明文里没有 `appid`；mock/dev 没配商户号）；
  · 生产 `validate_production` 保证两个键非空，所以"跳过"不会在生产发生。

用例全部走真实 HTTP 回调入口（`/api/pay/wechat/notify`）+ mock 网关明文报文，
**去修复即红**：删掉两处 `_merchant_guard` 调用后，错商户的报文会被照常入账。
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.common.gateways.payment.types import yuan_to_cents
from backend.config import get_settings
from tests.unit.test_wm12_payment import _bind_openid, _family, _online_order

MCH = "1900000109"
APPID = "wxaabbccddeeff0011"


def _post_notify(
    client: TestClient,
    *,
    payload: dict,
    event_type: str = "TRANSACTION.SUCCESS",
) -> object:
    """走真实回调入口（mock 网关把 ciphertext 当明文 JSON 解析）。"""
    body = json.dumps(
        {
            "id": "evt_b17",
            "event_type": event_type,
            "resource_type": "encrypt-resource",
            "resource": {
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": json.dumps(payload),
                "associated_data": "transaction",
                "nonce": "nonce123456",
            },
        }
    )
    return client.post(
        "/api/pay/wechat/notify",
        content=body,
        headers={
            "content-type": "application/json",
            "Wechatpay-Signature": "mock_sign",
            "Wechatpay-Timestamp": str(int(time.time())),
            "Wechatpay-Nonce": "nonce123456",
        },
    )


@pytest.fixture
def merchant_configured(monkeypatch):
    """给 settings 配上商户号/AppID（dev 默认是空的，等价于"没配"）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "WECHAT_MCH_ID", MCH)
    monkeypatch.setattr(settings, "WECHAT_APP_ID", APPID)
    return settings


def _db():
    from backend.database import SessionLocal

    return SessionLocal()


def _paid_order(client: TestClient) -> tuple[dict, dict]:
    """建家长+孩子 → 绑 openid → 下一笔**待支付**的线上订单，返回 (child, order 行快照)。

    订单字段从库里取（不信接口返回的键名），后续回调报文用它拼。
    """
    parent, child, mh = _family(client, phone="13800009931", name="B1-7 家长")
    db = _db()
    try:
        _bind_openid(db, parent["id"])
    finally:
        db.close()
    created = _online_order(client, mh, child["id"])
    db = _db()
    try:
        from backend.domain.identity.models import Order

        row = (
            db.query(Order).filter(Order.id == created["order_id"]).first()
            if "order_id" in created
            else None
        )
        if row is None:
            row = db.query(Order).order_by(Order.id.desc()).first()
        return child, {"id": row.id, "order_no": row.order_no, "amount": str(row.amount)}
    finally:
        db.close()


def _order_status(order_id: int) -> str:
    from backend.domain.identity.models import Order

    db = _db()
    try:
        return db.query(Order).filter(Order.id == order_id).first().status
    finally:
        db.close()


# ---------------------------------------------------------------- 支付回调


def test_payment_callback_wrong_mchid_is_rejected(client: TestClient, merchant_configured):
    """别的商户号的支付成功回调 → 400「商户信息不符」，订单**不得**变 paid。"""
    child, order = _paid_order(client)
    resp = _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_wrong_mch",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
            "mchid": "1999999999",  # 不是本系统的商户号
            "appid": APPID,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "商户" in resp.json()["message"]
    assert _order_status(order["id"]) != "paid", "错商户的回调把订单入账了（B1-7 未修）"


def test_payment_callback_wrong_appid_is_rejected(client: TestClient, merchant_configured):
    """同一商户号、但 AppID 不是本小程序 → 同样拒绝（两个字段都要核）。"""
    child, order = _paid_order(client)
    resp = _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_wrong_app",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
            "mchid": MCH,
            "appid": "wxsomeothermidapp",
        },
    )
    assert resp.status_code == 400, resp.text
    assert _order_status(order["id"]) != "paid"


def test_payment_callback_correct_merchant_is_credited(client: TestClient, merchant_configured):
    """对照组：商户号与 AppID 都对 → 正常入账（防"校验过严把真回调也拦死"）。"""
    child, order = _paid_order(client)
    resp = _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_ok",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
            "mchid": MCH,
            "appid": APPID,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _order_status(order["id"]) == "paid"


def test_payment_callback_without_merchant_fields_still_works(
    client: TestClient, merchant_configured
):
    """老报文/演练报文没带 mchid/appid → 跳过该项校验，照常入账（向后兼容）。"""
    child, order = _paid_order(client)
    resp = _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_no_fields",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
        },
    )
    assert resp.status_code == 200, resp.text
    assert _order_status(order["id"]) == "paid"


def test_guard_is_skipped_when_merchant_not_configured(client: TestClient, monkeypatch):
    """没配商户号（dev/mock 场景）→ 不校验。生产由 `validate_production` 保证非空。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "WECHAT_MCH_ID", "")
    monkeypatch.setattr(settings, "WECHAT_APP_ID", "")
    child, order = _paid_order(client)
    resp = _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_unconfigured",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
            "mchid": "1999999999",
            "appid": "wxsomeothermidapp",
        },
    )
    assert resp.status_code == 200, resp.text
    assert _order_status(order["id"]) == "paid"


# ---------------------------------------------------------------- 退款回调


def _refund_in_processing(client: TestClient) -> tuple[dict, dict]:
    """造一笔**已提交网关、等待回调**的线上退款单（processing + 有 out_refund_no）。"""
    from backend.domain.identity.models import Order, RefundRequest

    child, order = _paid_order(client)
    # 用真实支付回调把订单置 paid
    _post_notify(
        client,
        payload={
            "out_trade_no": order["order_no"],
            "transaction_id": "txn_b17_refund_base",
            "trade_state": "SUCCESS",
            "amount": int(yuan_to_cents(Decimal(str(order["amount"])))),
            "mchid": MCH,
            "appid": APPID,
        },
    )
    db = _db()
    try:
        o = db.query(Order).filter(Order.id == order["id"]).first()
        assert o.status == "paid"
        rr = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=o.id,
            child_id=child["id"],
            amount=o.amount,
            reason="B1-7 退款回调探针",
            status=RefundRequest.STATUS_PROCESSING,
            out_refund_no=f"RF{o.id}-1-1",
        )
        db.add(rr)
        db.flush()
        row = {"id": rr.id, "out_refund_no": rr.out_refund_no, "amount": str(rr.amount)}
        db.commit()
    finally:
        db.close()
    return order, row


def _refund_status(req_id: int) -> str:
    from backend.domain.identity.models import RefundRequest

    db = _db()
    try:
        return db.query(RefundRequest).filter(RefundRequest.id == req_id).first().status
    finally:
        db.close()


def test_refund_callback_wrong_mchid_does_not_finalize(client: TestClient, merchant_configured):
    """错商户号的退款成功通知 → 400，退款单**保持 processing**（不落 refunded）。"""
    order, rr = _refund_in_processing(client)
    resp = _post_notify(
        client,
        event_type="REFUND.SUCCESS",
        payload={
            "out_trade_no": order["order_no"],
            "out_refund_no": rr["out_refund_no"],
            "refund_status": "SUCCESS",
            "refund_amount": int(yuan_to_cents(Decimal(rr["amount"]))),
            "mchid": "1999999999",
        },
    )
    assert resp.status_code == 400, resp.text
    assert _refund_status(rr["id"]) == "processing", "错商户的退款回调把退款单落终态了（B1-7 未修）"


def test_refund_callback_correct_mchid_finalizes(client: TestClient, merchant_configured):
    """对照组：商户号正确 → 退款单落 refunded（退款通知不带 appid，不得因此被拒）。"""
    order, rr = _refund_in_processing(client)
    resp = _post_notify(
        client,
        event_type="REFUND.SUCCESS",
        payload={
            "out_trade_no": order["order_no"],
            "out_refund_no": rr["out_refund_no"],
            "refund_status": "SUCCESS",
            "refund_amount": int(yuan_to_cents(Decimal(rr["amount"]))),
            "mchid": MCH,
        },
    )
    assert resp.status_code == 200, resp.text
    assert _refund_status(rr["id"]) == "refunded"


def test_refund_notify_has_no_appid_and_must_not_be_rejected(
    client: TestClient, merchant_configured
):
    """微信退款通知明文里**没有 appid**——校验必须容得下"字段缺失"，否则真回调全被拒。"""
    order, rr = _refund_in_processing(client)
    resp = _post_notify(
        client,
        event_type="REFUND.SUCCESS",
        payload={
            "out_trade_no": order["order_no"],
            "out_refund_no": rr["out_refund_no"],
            "refund_status": "SUCCESS",
            "refund_amount": int(yuan_to_cents(Decimal(rr["amount"]))),
            "mchid": MCH,
            # 故意不带 appid
        },
    )
    assert resp.status_code == 200, resp.text
    assert _refund_status(rr["id"]) == "refunded"


# ---------------------------------------------------------------- 直测守卫（口径单位）


@pytest.mark.parametrize(
    ("mchid", "appid", "expected"),
    [
        (MCH, APPID, None),
        ("1999999999", APPID, 400),
        (MCH, "wxother", 400),
        ("", "", None),
        ("", APPID, None),  # 退款通知形态：只有 appid 缺失
    ],
)
def test_merchant_guard_matrix(client: TestClient, merchant_configured, mchid, appid, expected):
    """守卫本身的判定矩阵（配置侧都有值）。"""
    from backend.common.gateways.payment.types import PaymentCallbackData
    from backend.domain.identity.payment_service import PaymentService

    db = _db()
    try:
        svc = PaymentService(db)
        data = PaymentCallbackData(out_trade_no="X", mchid=mchid, appid=appid)
        got = svc._merchant_guard(data, scene="探针")
        assert (got[0] if got else None) == expected, (mchid, appid, got)
        db.rollback()
    finally:
        db.close()
