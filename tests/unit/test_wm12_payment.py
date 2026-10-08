# tests/unit/test_wm12_payment.py — 微信线上支付（WM12-A：收款侧闭环 + 三件套）
"""断言的是**钱**的逻辑，不是 UI：

① 下单：在线单进 `pending_payment`；金额服务端算（配置同源；二孩折扣在下单时刻判定）
② 预支付：三段式（状态先落库 → 事务外调网关 → 锁内复核）；无 openid 拒付；mock 通道即时到账
   走**与真实回调同一条结算链**（`_settle_paid`），所以"即时到账"不能被当成另一条旁路
③ 回调三件套（模式手册 P3）：验签失败拒（401，且 fail-closed）/ 金额不符拒（400，订单仍待支付）/
   重复回调 200 且不重复入账（会员到期日不变、押金只记一次）
④ 乱序与迟到：已退款订单忽略支付回调（状态不反转）；已取消订单收到成功回调 → 留痕待人工
⑤ 幂等落库：同一 `transaction_id` 不得挂两笔订单（唯一索引 + 服务层查重）
⑥ 生产校验：生产禁 mock 支付通道；接微信支付缺件拒启
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest

from backend.common.gateways.payment.types import yuan_to_cents
from backend.domain.identity.models import Child, Order

DEV_CODE = "1234"  # .env 默认 LOGIN_DEV_CODE（生产置空后此测试无意义，见 config 校验）


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, phone="13800009901", name="支付测试家长"):
    """建档（家长+孩子）+ 家长登录态（小程序 token）。"""
    h = _h(client)
    p = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert p.status_code == 200, p.text
    parent = p.json()
    c = client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "支付测试孩"},
        headers=h,
    )
    assert c.status_code == 200, c.text
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": DEV_CODE})
    assert login.status_code == 200, login.text
    mh = {"Authorization": f"Bearer {login.json()['token']}"}
    return parent, c.json(), mh


def _bind_openid(db, parent_id: int, openid="o_test_openid_0001") -> None:
    """给家长绑上微信 openid（真实场景来自微信一键登录绑定；这里直写库省掉编造 code）。"""
    from backend.domain.identity.models import Parent

    p = db.query(Parent).filter(Parent.id == parent_id).first()
    p.wechat_openid = openid
    db.commit()


def _online_order(client, mh, child_id, order_type="observation_fee"):
    r = client.post(
        "/api/miniapp/orders",
        json={"child_id": child_id, "order_type": order_type},
        headers=mh,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _notify(
    client,
    order_no,
    amount_yuan,
    transaction_id="txn_1001",
    trade_state="SUCCESS",
    signature="mock_sign",
    event_type="TRANSACTION.SUCCESS",
):
    payload = {
        "out_trade_no": order_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "amount": int(yuan_to_cents(Decimal(str(amount_yuan)))),
    }
    body = json.dumps(
        {
            "id": "evt_1",
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
            "Wechatpay-Signature": signature,
            "Wechatpay-Timestamp": str(int(time.time())),
            "Wechatpay-Nonce": "nonce123456",
        },
    )


# ---------- ① 价格与下单 ----------


def test_plans_prices_come_from_config(client):
    """购买页价格 = 配置值（前端零硬编码）；99 元首场可用性随资格动态变化。"""
    _parent, child, mh = _family(client)
    r = client.get(f"/api/miniapp/payment/plans?child_id={child['id']}", headers=mh)
    assert r.status_code == 200, r.text
    plans = r.json()
    assert plans["payment_enabled"] is True
    by_type = {i["order_type"]: i for i in plans["items"]}
    assert Decimal(by_type["observation_fee"]["amount"]) == Decimal("500")
    assert Decimal(by_type["formal_fee"]["amount"]) == Decimal("6000")
    assert Decimal(by_type["deposit"]["amount"]) == Decimal("1200")
    assert Decimal(by_type["first_activity_fee"]["amount"]) == Decimal("99")
    assert by_type["first_activity_fee"]["available"] is True


def test_online_order_enters_pending_payment(client, db):
    """在线单直接进 `pending_payment`（此前该状态在代码里从无赋值）；金额由服务端算。"""
    parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    assert order["status"] == Order.STATUS_PENDING_PAYMENT
    assert Decimal(order["amount"]) == Decimal("500")
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    assert row.status == Order.STATUS_PENDING_PAYMENT
    assert row.paid_at is None
    assert row.transaction_id is None


def test_online_order_secondary_child_discount(client, db):
    """二孩折扣按下单时刻判定（V1.1 §3.1）：先让老大成为正式会员，老二年费自动 9 折。"""
    parent, child1, mh = _family(client, phone="13800009902")
    h = _h(client)
    c2 = client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": "老二"},
        headers=h,
    ).json()
    # 老大走线上支付转正式会员（mock 即时到账）
    _bind_openid(db, parent["id"])
    formal = _online_order(client, mh, child1["id"], "formal_fee")
    assert Decimal(formal["amount"]) == Decimal("6000")  # 无兄弟会员 → 原价
    pay = client.post(f"/api/miniapp/orders/{formal['id']}/pay", headers=mh)
    assert pay.status_code == 200, pay.text
    assert pay.json()["instant_paid"] is True
    # 老二年费 → 9 折
    plans = client.get(f"/api/miniapp/payment/plans?child_id={c2['id']}", headers=mh).json()
    by_type = {i["order_type"]: i for i in plans["items"]}
    assert Decimal(by_type["formal_fee"]["amount"]) == Decimal("5400.00")


def test_online_order_type_not_supported(client):
    """管理端专属类型（自定义单/活动费）不能从家长端在线下单。"""
    _parent, child, mh = _family(client)
    r = client.post(
        "/api/miniapp/orders",
        json={"child_id": child["id"], "order_type": "custom"},
        headers=mh,
    )
    assert r.status_code == 422


# ---------- ② 预支付（三段式 + mock 即时到账） ----------


def test_prepay_requires_openid(client):
    """没绑微信 openid → 拒付并说清原因（否则微信会返回 APPID_MCHID_NOT_MATCH 之类的怪错）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    r = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh)
    assert r.status_code == 422
    assert "微信" in r.json()["detail"]


def test_prepay_mock_instant_settles_membership(client, db):
    """mock 通道即时到账：订单转 paid + 会员开通 + `transaction_id` 落库（与回调同一结算链）。"""
    parent, child, mh = _family(client)
    _bind_openid(db, parent["id"])
    order = _online_order(client, mh, child["id"])
    r = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["instant_paid"] is True and body["already_paid"] is False
    assert body["status"] == Order.STATUS_PAID

    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PAID
    assert row.pay_method == "wechat"
    assert row.transaction_id and row.transaction_id.startswith("mock_txn_")
    assert row.paid_by is None  # 线上支付没有经办人
    kid = db.query(Child).filter(Child.id == child["id"]).first()
    db.refresh(kid)
    assert kid.member_status == Child.MEMBER_OBSERVATION
    assert kid.member_expire is not None


def test_prepay_already_paid_is_idempotent(client, db):
    """已支付订单再点「去支付」→ 直接告知已支付，不产生第二次入账。"""
    parent, child, mh = _family(client)
    _bind_openid(db, parent["id"])
    order = _online_order(client, mh, child["id"])
    first = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh).json()
    second = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh).json()
    assert first["status"] == Order.STATUS_PAID
    assert second["already_paid"] is True
    assert db.query(Order).filter(Order.order_no == order["order_no"]).count() == 1


def test_prepay_other_parents_order_not_found(client, db):
    """越权：别的家长的订单不能发起支付（404，不泄露存在性）。"""
    _p1, _c1, _mh1 = _family(client, phone="13800009903", name="家长甲")
    _p2, child2, mh2 = _family(client, phone="13800009904", name="家长乙")
    order = _online_order(client, mh2, child2["id"])
    r = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=_mh1)
    assert r.status_code == 404


# ---------- ③ 回调三件套 ----------


def test_callback_settles_order_and_membership(client, db):
    """回调成功 → 订单 paid + 会员开通 + 流水号落库 + 审计（全程无人工确认）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    r = _notify(client, order["order_no"], "500.00", transaction_id="txn_ok_1")
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "SUCCESS"

    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PAID
    assert row.transaction_id == "txn_ok_1"
    assert row.pay_method == "wechat"
    kid = db.query(Child).filter(Child.id == child["id"]).first()
    db.refresh(kid)
    assert kid.member_status == Child.MEMBER_OBSERVATION


def test_duplicate_callback_does_not_double_settle(client, db):
    """重复回调（同一流水号连发两次）→ 第二次 200 但**不重复入账**（会员到期日不变）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    assert _notify(client, order["order_no"], "500.00", transaction_id="txn_dup").status_code == 200
    kid = db.query(Child).filter(Child.id == child["id"]).first()
    db.refresh(kid)
    expire_after_first = kid.member_expire

    r2 = _notify(client, order["order_no"], "500.00", transaction_id="txn_dup")
    assert r2.status_code == 200 and r2.json()["code"] == "SUCCESS"
    db.refresh(kid)
    assert kid.member_expire == expire_after_first  # 没有被顺延/重开

    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.transaction_id == "txn_dup"


def test_duplicate_callback_deposit_ledger_single_entry(client, db):
    """押金单重复回调：押金账本只有一条入账（钱不能记两次）。"""
    parent, child, mh = _family(client)
    _bind_openid(db, parent["id"])
    order = _online_order(client, mh, child["id"], "deposit")
    assert (
        _notify(client, order["order_no"], "1200.00", transaction_id="txn_dep").status_code == 200
    )
    _notify(client, order["order_no"], "1200.00", transaction_id="txn_dep")
    from backend.domain.billing.models import Deposit, DepositLedger

    dep = db.query(Deposit).filter(Deposit.child_id == child["id"]).first()
    assert dep is not None and dep.status == Deposit.STATUS_PAID
    assert db.query(DepositLedger).filter(DepositLedger.deposit_id == dep.id).count() == 1


def test_callback_amount_mismatch_rejected(client, db):
    """金额不符 → 拒绝入账（订单仍待支付）+ 审计留痕（钱的事必须查得到）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    r = _notify(client, order["order_no"], "1.00", transaction_id="txn_bad_amount")
    assert r.status_code == 400
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PENDING_PAYMENT
    assert row.transaction_id is None
    assert _anomaly_count(db, "金额") >= 1


def test_callback_non_success_state_ignored(client, db):
    """trade_state 消费：非 SUCCESS（如 CLOSED）不入账，但应答 200 免重试风暴。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    r = _notify(
        client, order["order_no"], "500.00", transaction_id="txn_closed", trade_state="CLOSED"
    )
    assert r.status_code == 200
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PENDING_PAYMENT


def test_callback_signature_failure_fail_closed(client, monkeypatch):
    """验签不可用/不通过 → 401 拒绝（生产缺平台证书时必须拒，绝不静默放行）。"""
    from backend.integrations import payment as payment_pkg

    class _BadSignatureGateway:
        async def verify_callback_signature(self, body, signature, timestamp, nonce):
            return False

    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: _BadSignatureGateway())
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    r = _notify(client, order["order_no"], "500.00", transaction_id="txn_badsig")
    assert r.status_code == 401

    class _BrokenGateway:
        async def verify_callback_signature(self, body, signature, timestamp, nonce):
            raise RuntimeError("微信平台证书未配置，无法验签")

    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: _BrokenGateway())
    r2 = _notify(client, order["order_no"], "500.00", transaction_id="txn_nocert")
    assert r2.status_code == 401


def test_callback_transaction_id_reuse_rejected(client, db):
    """同一微信流水号不能挂两笔订单（DB 唯一索引 + 服务层查重双保险）。"""
    _parent, child, mh = _family(client)
    o1 = _online_order(client, mh, child["id"])
    o2 = _online_order(client, mh, child["id"], "deposit")
    assert _notify(client, o1["order_no"], "500.00", transaction_id="txn_shared").status_code == 200
    r = _notify(client, o2["order_no"], "1200.00", transaction_id="txn_shared")
    assert r.status_code == 409
    row2 = db.query(Order).filter(Order.order_no == o2["order_no"]).first()
    db.refresh(row2)
    assert row2.status == Order.STATUS_PENDING_PAYMENT


# ---------- ④ 乱序与迟到 ----------


def test_refunded_order_ignores_pay_callback(client, db):
    """乱序守卫：已退款订单收到支付回调 → 忽略（防资金状态反转）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    assert _notify(client, order["order_no"], "500.00", transaction_id="txn_r1").status_code == 200
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    row.status = Order.STATUS_REFUNDED
    row.refund_status = Order.REFUND_STATUS_REFUNDED
    db.commit()

    r = _notify(client, order["order_no"], "500.00", transaction_id="txn_r2")
    assert r.status_code == 200 and r.json()["message"] == "已忽略"
    db.refresh(row)
    assert row.status == Order.STATUS_REFUNDED  # 没被反转回 paid
    assert row.transaction_id == "txn_r1"


def test_cancelled_order_late_payment_flagged(client, db):
    """迟到支付：已取消订单收到成功回调 → 应答 200 但**留痕待人工**（钱收了不能静默）。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    row.status = Order.STATUS_CANCELLED
    db.commit()

    r = _notify(client, order["order_no"], "500.00", transaction_id="txn_late")
    assert r.status_code == 200
    assert "人工" in r.json()["message"]
    db.refresh(row)
    assert row.status == Order.STATUS_CANCELLED
    assert _anomaly_count(db, "迟到支付") >= 1


def test_callback_unknown_order_rejected(client):
    """查无此单 → FAIL（异常要暴露给运维，不假装成功）。"""
    r = _notify(client, "DMK00000000000000XXXX", "500.00", transaction_id="txn_ghost")
    assert r.status_code == 404


# ---------- ⑤ 演练通道与任务 ----------


def test_simulate_callback_superadmin_only(client):
    """演练回调解调：超管可用（mock 通道）；专员 403。"""
    staff = _h(client, username="staff01")
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    body = {"order_no": order["order_no"], "trade_state": "SUCCESS"}

    denied = client.post("/api/admin/payments/simulate-callback", json=body, headers=staff)
    assert denied.status_code == 403

    h = _h(client)
    ok = client.post("/api/admin/payments/simulate-callback", json=body, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["http_status"] == 200
    assert ok.json()["response"]["code"] == "SUCCESS"


def test_simulate_callback_replay_same_transaction_id(client, db):
    """演练通道可用于验收 S4：同一 `transaction_id` 连发两次 → 第二次不重复入账。"""
    _parent, child, mh = _family(client)
    order = _online_order(client, mh, child["id"])
    h = _h(client)
    body = {"order_no": order["order_no"], "transaction_id": "txn_sim_1"}
    first = client.post("/api/admin/payments/simulate-callback", json=body, headers=h).json()
    assert first["response"]["code"] == "SUCCESS"
    kid = db.query(Child).filter(Child.id == child["id"]).first()
    db.refresh(kid)
    expire = kid.member_expire
    second = client.post("/api/admin/payments/simulate-callback", json=body, headers=h).json()
    assert second["response"]["code"] == "SUCCESS"
    db.refresh(kid)
    assert kid.member_expire == expire


def test_simulate_callback_forbidden_on_wechat_provider(client, monkeypatch):
    """真通道下演练端点必须不可用（否则等于生产开了一键伪造支付的后门）。"""
    from backend.config import get_settings

    monkeypatch.setenv("PAYMENT_PROVIDER", "wechat")
    get_settings.cache_clear()
    try:
        _parent, child, mh = _family(client)
        order = _online_order(client, mh, child["id"])
        h = _h(client)
        r = client.post(
            "/api/admin/payments/simulate-callback",
            json={"order_no": order["order_no"]},
            headers=h,
        )
        assert r.status_code == 403
    finally:
        monkeypatch.delenv("PAYMENT_PROVIDER", raising=False)
        get_settings.cache_clear()


def test_cert_refresh_task_is_noop_on_mock(db):
    """证书轮换任务：非微信支付通道 → 0（no-op），不会因缺证书把任务跑红。"""
    from backend.tasks.registry import _wechat_cert_refresh

    assert _wechat_cert_refresh(db) == 0


# ---------- ⑥ 生产校验（审查 P2-5） ----------


def _prod_settings(**overrides):
    """构造"其余都合规"的生产配置（直接构造 Settings 而非改环境变量：

    改环境变量 + `get_settings.cache_clear()` 会波及**全局**——连 autouse 的播种 fixture
    都会读到 DEBUG=false 而拒绝播默认口令，测试之间互相污染。直接构造就没这个问题。
    """
    from backend.config import Settings

    base = dict(
        DEBUG=False,
        SECRET_KEY="x" * 32,
        DB_PASSWORD="pw",
        WECHAT_APP_ID="wx",
        WECHAT_APP_SECRET="sec",
        LOGIN_DEV_CODE="",
        CORS_ORIGINS="https://admin.example.com",
        SMS_ENABLED=False,
        PAYMENT_ENABLED=True,
        PAYMENT_PROVIDER="mock",
    )
    base.update(overrides)
    return Settings(**base)


def test_production_rejects_mock_payment():
    """生产禁 mock 支付通道：验签永远放行 = 任何人可伪造回调把自己的订单刷成已支付。"""
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER"):
        _prod_settings(PAYMENT_PROVIDER="mock").validate_production()


def test_production_requires_payment_credentials():
    """接微信支付但缺件 → 拒启，并逐项列出缺什么。"""
    with pytest.raises(RuntimeError, match="微信支付配置缺失"):
        _prod_settings(PAYMENT_PROVIDER="wechat").validate_production()


def test_production_rejects_http_notify_url(tmp_path):
    """回调地址必须 https（微信只回调 https），且商户私钥文件必须真的存在。"""
    key = tmp_path / "apiclient_key.pem"
    key.write_text("dummy")
    with pytest.raises(RuntimeError, match="https"):
        _prod_settings(
            PAYMENT_PROVIDER="wechat",
            WECHAT_MCH_ID="1900000001",
            WECHAT_API_KEY_V3="k" * 32,
            WECHAT_CERT_SERIAL_NO="ABCDEF",
            WECHAT_PRIVATE_KEY_PATH=str(key),
            WECHAT_PLATFORM_CERT_PATH=str(tmp_path / "platform.pem"),
            WECHAT_PAY_NOTIFY_URL="http://api.example.com/api/pay/wechat/notify",
            WECHAT_REFUND_NOTIFY_URL="https://api.example.com/api/pay/wechat/notify",
        ).validate_production()
    with pytest.raises(RuntimeError, match="私钥文件不存在"):
        _prod_settings(
            PAYMENT_PROVIDER="wechat",
            WECHAT_MCH_ID="1900000001",
            WECHAT_API_KEY_V3="k" * 32,
            WECHAT_CERT_SERIAL_NO="ABCDEF",
            WECHAT_PRIVATE_KEY_PATH=str(tmp_path / "nope.pem"),
            WECHAT_PLATFORM_CERT_PATH=str(tmp_path / "platform.pem"),
            WECHAT_PAY_NOTIFY_URL="https://api.example.com/api/pay/wechat/notify",
            WECHAT_REFUND_NOTIFY_URL="https://api.example.com/api/pay/wechat/notify",
        ).validate_production()  # 不抛即过


def test_production_accepts_complete_payment_config(tmp_path):
    """四件套齐备 + https 回调 → 放行（fail-closed 不误伤合规部署）。"""
    key = tmp_path / "apiclient_key.pem"
    key.write_text("dummy")
    _prod_settings(
        PAYMENT_PROVIDER="wechat",
        WECHAT_MCH_ID="1900000001",
        WECHAT_API_KEY_V3="k" * 32,
        WECHAT_CERT_SERIAL_NO="ABCDEF",
        WECHAT_PRIVATE_KEY_PATH=str(key),
        # 平台证书允许"首刷补齐"（部署后跑一次轮换任务），所以此处不要求文件已存在
        WECHAT_PLATFORM_CERT_PATH=str(tmp_path / "platform.pem"),
        WECHAT_PAY_NOTIFY_URL="https://api.example.com/api/pay/wechat/notify",
        WECHAT_REFUND_NOTIFY_URL="https://api.example.com/api/pay/wechat/notify",
    ).validate_production()  # 不抛即过


def test_production_manual_only_branch():
    """上线版本若选「纯人工收款」：PAYMENT_ENABLED=false 即可放行（支付入口随之关闭）。"""
    _prod_settings(PAYMENT_ENABLED=False, PAYMENT_PROVIDER="mock").validate_production()


# ---------- 工具 ----------


def _anomaly_count(db, keyword: str) -> int:
    """支付异常审计条数：`payment.anomaly` 且 message/reason 含关键词。

    两个坑：① 关键词写在 `detail.message` 里（`reason` 装的是金额/流水号这类原始值）；
    ② MySQL 默认 REPEATABLE READ——测试会话先 `commit()` 结束快照，否则读不到
    服务端另一个会话刚提交的审计行。
    """
    from backend.common.system_models import AuditLog

    db.commit()
    rows = db.query(AuditLog).filter(AuditLog.action == "payment.anomaly").all()
    return sum(
        1
        for r in rows
        if keyword in (r.reason or "") or keyword in json.dumps(r.detail or {}, ensure_ascii=False)
    )
