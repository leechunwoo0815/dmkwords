# tests/unit/test_wm12_review_fixes.py — WM12-C 整改固化断言（外部专家审查 19 项）
"""每条对上一个验收判据（`docs/09` WM12-C §三）：

P0-1 未知态：网关超时 → 退款单留"执行中"+未知态（不落 failed）；未知态重提 422；查单后才放行；
     对账报"同一订单退款合计超付"（重复出款的兜底探测器）
P0-2 账单：跨日退款按 `out_refund_no` 核对（不再假差异）；商品名含逗号不错列；
     哈希失败记 failed（不是 skipped）；`hash_value` 缺失 fail-closed
P0-3 口径：未绑微信 → `can_pay_online=false` + prepay 422；登录载荷带 `wechat_bound`
P0-4 短信：**并发**错误码把 attempts 打满且超限请求被拒（原子条件更新，20 并发只记 4 次的历史缺陷）
P1-5 限流：伪造 `X-Forwarded-For` 不换桶；`TRUSTED_PROXY_COUNT>0` 才读（右起第 N 段）
P1-6 生产校验：`APP_ENV=production` + `DEBUG=true` 仍拦 mock 通道
P1-7 退款回调金额不符 → 不落终态
P2-8 退款台账 SQL 条数与行数无关
P2-11 专员可读对账报告（与后端同码）
P2-13 绑定并发穿透 → 422（不是 500）
P2-14 非法状态收到退款通知 → 200 忽略（不是 500 → 微信重发 24h）
P2-15 支付回调并发入账被唯一索引拦下 → 200 + 审计
P3-19 演练端点金额格式错 → 422
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from backend.domain.identity.models import Order, RefundRequest, SmsCode
from backend.domain.identity.payment_models import PaymentReconciliation
from backend.domain.identity.payment_reconcile_service import PaymentReconcileService
from tests.unit.test_login_wechat_sms import (
    _parent_with_child,
    _send_and_read_code,
)
from tests.unit.test_wm12_payment import _bind_openid, _family, _h, _notify, _online_order
from tests.unit.test_wm12_refund_reconcile import (
    _apply_refund,
    _approve,
    _execute,
    _online_paid_order,
    _refund_row,
)

# ---------- 工具 ----------


def _fail_gateway(
    monkeypatch, *, fail_times=1, query_found=None, query_state="", refund_state="SUCCESS"
):
    """退款网关桩：`refund` 抛超时（可只抛前 N 次），`query_refund` 按参数回结果。

    下单/回调仍走 mock（否则"线上支付成功"那步造不出来）。
    """
    from backend.common.gateways.payment.mock import MockPaymentGateway
    from backend.common.gateways.payment.types import PaymentRefundQuery, PaymentRefundResponse
    from backend.integrations import payment as payment_pkg

    class _Stub:
        def __init__(self):
            self._mock = MockPaymentGateway()
            self.refund_calls = 0
            self.query_calls = 0

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
            self.refund_calls += 1
            if self.refund_calls <= fail_times:
                raise TimeoutError("read timed out after 30s")
            return PaymentRefundResponse(success=True, refund_id="wx_ok", state=refund_state)

        async def query_refund(self, out_refund_no):
            self.query_calls += 1
            if query_found is None:
                raise NotImplementedError("未提供查单能力（模拟网关不支持）")
            return PaymentRefundQuery(found=query_found, state=query_state, refund_id="wx_q")

    stub = _Stub()
    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: stub)
    return stub


def _bill_csv(rows, header=None, tail="总交易单数,2") -> str:
    header = header or [
        "交易时间",
        "商户订单号",
        "商品名称",
        "商户退款单号",
        "交易状态",
        "订单金额",
        "退款金额",
        "退款状态",
    ]
    lines = [",".join(header)]
    lines += [",".join(r) for r in rows]
    if tail:
        lines.append(tail)
    return "\n".join(lines)


def _paid_today(client, db, phone="13800009961", order_type="observation_fee"):
    """今天线上支付成功的订单（账单比对里属于"当日已支付"）。"""
    return _online_paid_order(client, db, order_type=order_type, phone=phone)


def _set_paid_at(db, order, when: datetime) -> None:
    order.paid_at = when
    db.commit()


# ---------- P0-1 未知态 ----------


def test_gateway_timeout_keeps_processing_and_marks_unknown(client, db, monkeypatch):
    """网关抛超时 → 退款单**留在执行中** + 未知态标记（修复前落 failed，重试会二次出款）。"""
    _fail_gateway(monkeypatch, fail_times=1)
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009962")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    result = _execute(client, h, applied["id"], remark="原路退")

    assert result["channel"] == "wechat"
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING, "未知态必须留在执行中，不得落 failed"
    assert req.gateway_unknown_at is not None, "网关结果未知必须留标记"
    assert req.out_refund_no == f"RF{req.id}-1-1"
    db.refresh(order)
    assert order.status == Order.STATUS_PAID, "钱没退出去，订单不能显示已退款"


def test_unknown_refund_retry_is_rejected_without_query(client, db, monkeypatch):
    """未知态直接重提 → 422（防换新商户退款单号的二次出款）。"""
    _fail_gateway(monkeypatch, fail_times=1)
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009963")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    retry = client.post(
        f"/api/admin/refund-requests/{applied['id']}/execute",
        json={"success": True, "remark": "再试一次"},
        headers=h,
    )
    assert retry.status_code == 422, retry.text
    assert "查单" in retry.json()["detail"]
    req = _refund_row(db, applied["id"])
    assert req.gateway_attempts == 1, "被拒的重提不得消耗新的商户退款单号"


def test_query_gateway_confirms_not_accepted_then_retry_ok(client, db, monkeypatch):
    """查单结论"微信侧查无此单" → 置失败（可安全重试）→ 重试成功落已退款。"""
    stub = _fail_gateway(monkeypatch, fail_times=1, query_found=False)
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009964")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    q = client.post(f"/api/admin/refund-requests/{applied['id']}/query-gateway", headers=h)
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["gateway_state"] == "NOT_FOUND" and body["resolved"] is True
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_FAILED
    assert req.gateway_unknown_at is None, "查单确认未受理后未知态必须解除"
    assert stub.query_calls == 1

    again = _execute(client, h, applied["id"], remark="查单后重试")
    assert again["channel"] == "wechat"
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    assert req.out_refund_no == f"RF{req.id}-2-1", "重试换新单号（上一笔已确认未受理）"


def test_query_gateway_success_settles_refund(client, db, monkeypatch):
    """查单结论 SUCCESS（回调没到）→ 直接落已退款。"""
    _fail_gateway(monkeypatch, fail_times=1, query_found=True, query_state="SUCCESS")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009965")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    q = client.post(f"/api/admin/refund-requests/{applied['id']}/query-gateway", headers=h)
    assert q.status_code == 200, q.text
    assert q.json()["gateway_state"] == "SUCCESS"
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    db.refresh(order)
    assert order.status == Order.STATUS_REFUNDED


def test_query_gateway_unavailable_keeps_unknown(client, db, monkeypatch):
    """查单能力不可用（网关不支持/网络失败）→ 未知态保持、状态不动（宁可不猜）。"""
    _fail_gateway(monkeypatch, fail_times=1)  # query_found=None → 桩抛 NotImplementedError
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009966")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")

    q = client.post(f"/api/admin/refund-requests/{applied['id']}/query-gateway", headers=h)
    assert q.status_code == 200, q.text
    assert q.json()["resolved"] is False
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING and req.gateway_unknown_at is not None


def test_reconcile_flags_refund_overpaid(client, db):
    """同一订单两笔活跃退款合计 > 订单金额 → 对账必报（重复出款的兜底探测器）。"""
    parent, child, mh, order = _online_paid_order(client, db, phone="13800009967")
    assert str(order.amount) == "500.00"
    for _ in range(2):
        db.add(
            RefundRequest(
                kind=RefundRequest.KIND_ORDER,
                order_id=order.id,
                child_id=child["id"],
                amount=Decimal("300.00"),
                reason="造单：超付场景",
                status=RefundRequest.STATUS_REFUNDED,
            )
        )
    db.commit()

    report = PaymentReconcileService(db).run(with_wechat=False)["reports"][0]
    kinds = {d["kind"] for d in report["detail"]}
    assert "refund_overpaid" in kinds, report
    overpaid = [d for d in report["detail"] if d["kind"] == "refund_overpaid"][0]
    assert overpaid["ref"] == order.order_no
    assert "600.00" in overpaid["message"] and "500.00" in overpaid["message"]


def test_reconcile_overpaid_ignores_rejected_and_cancelled(client, db):
    """被拒/被撤销的退款单不计入"合计"（否则每天一条假差异）。"""
    _parent, child, _mh, order = _online_paid_order(client, db, phone="13800009968")
    db.add(
        RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order.id,
            child_id=child["id"],
            amount=Decimal("400.00"),
            reason="造单：被拒",
            status=RefundRequest.STATUS_REJECTED,
        )
    )
    db.commit()
    report = PaymentReconcileService(db).run(with_wechat=False)["reports"][0]
    assert "refund_overpaid" not in {d["kind"] for d in report["detail"]}


# ---------- P0-2 账单比对 ----------


def test_bill_cross_day_refund_checked_via_out_refund_no(client, db):
    """跨日退款：不产生假 bill_only，且退款金额/状态**真的被校验**（修复前永远不校验）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009971")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED
    _set_paid_at(db, order, datetime.now() - timedelta(days=1))  # 支付在昨天、退款在今天
    out_refund_no = req.out_refund_no

    service = PaymentReconcileService(db)
    csv_text = _bill_csv(
        [
            [
                "2026-10-09 10:00:00",
                order.order_no,
                "观察期会员费",
                out_refund_no,
                "REFUND",
                "500.00",
                "500.00",
                "SUCCESS",
            ]
        ]
    )
    checked, diffs = service._diff_bill(date(2026, 10, 9), csv_text)
    assert checked == 1
    assert diffs == [], f"跨日退款不该有假差异：{diffs}"

    # 金额不符必须被抓出来（修复前这条路径根本不会执行）
    bad = _bill_csv(
        [
            [
                "2026-10-09 10:00:00",
                order.order_no,
                "观察期会员费",
                out_refund_no,
                "REFUND",
                "500.00",
                "10.00",
                "SUCCESS",
            ]
        ]
    )
    _checked, diffs_bad = service._diff_bill(date(2026, 10, 9), bad)
    assert [d["kind"] for d in diffs_bad] == ["refund_amount_mismatch"], diffs_bad

    # 本地查不到的退款单号 → 显式报"账单有我库无"
    unknown = _bill_csv(
        [
            [
                "2026-10-09 10:00:00",
                order.order_no,
                "观察期会员费",
                "RF999999-1-1",
                "REFUND",
                "500.00",
                "500.00",
                "SUCCESS",
            ]
        ]
    )
    _checked2, diffs_unknown = service._diff_bill(date(2026, 10, 9), unknown)
    assert [d["kind"] for d in diffs_unknown] == ["bill_refund_only"], diffs_unknown


def test_bill_parse_handles_quoted_comma_in_product_name():
    """商品名含逗号（带引号）→ csv 解析不错列（修复前列整体错位，金额读到别列）。"""
    csv_text = _bill_csv(
        [
            [
                "2026-10-09 10:00:00",
                "DMK20261009001",
                '"绘本套装,上册,下册"',
                "",
                "SUCCESS",
                "99.00",
                "0.00",
                "",
            ]
        ]
    )
    rows = PaymentReconcileService._parse_bill(csv_text)
    assert len(rows) == 1
    assert rows[0]["商品名称"] == "绘本套装,上册,下册"
    assert rows[0]["订单金额"] == "99.00", "列错位时这里会读到费率/其它列"


def test_bill_comma_row_does_not_fake_amount_mismatch(client, db):
    """商品名含逗号的账单行与本地订单比对 → 无假差异。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009972")
    csv_text = _bill_csv(
        [
            [
                "2026-10-09 10:00:00",
                order.order_no,
                '"绘本套装,上册,下册"',
                "",
                "SUCCESS",
                str(order.amount),
                "0.00",
                "",
            ]
        ]
    )
    _checked, diffs = PaymentReconcileService(db)._diff_bill(date.today(), csv_text)
    assert diffs == [], diffs


def test_bill_hash_failure_recorded_as_failed_not_skipped(client, db, monkeypatch):
    """哈希校验失败 → failed（修复前因为文案含"账单"被记成 skipped，运营读成没问题）。"""
    from backend.common.exceptions import PaymentError
    from backend.config import get_settings
    from backend.integrations import payment as payment_pkg

    monkeypatch.setattr(get_settings(), "PAYMENT_PROVIDER", "wechat")

    class _BillGateway:
        async def download_bill(self, bill_date, bill_type="ALL"):
            raise PaymentError("微信对账单哈希校验失败（文件可能被篡改或下载不完整）")

    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: _BillGateway())
    service = PaymentReconcileService(db)
    report = service._reconcile_with_wechat(
        date.today(), trigger=PaymentReconciliation.TRIGGER_MANUAL
    )
    assert report["status"] == PaymentReconciliation.STATUS_FAILED, report
    assert "不可信" in report["note"]

    class _NoBillGateway:
        async def download_bill(self, bill_date, bill_type="ALL"):
            raise PaymentError("微信对账单下载失败: NO_BILL_EXIST")

    monkeypatch.setattr(payment_pkg, "get_payment_gateway", lambda: _NoBillGateway())
    report2 = service._reconcile_with_wechat(
        date.today(), trigger=PaymentReconciliation.TRIGGER_MANUAL
    )
    assert report2["status"] == PaymentReconciliation.STATUS_SKIPPED, report2


# ---------- P0-3 支付口径 ----------


def test_plans_marks_can_pay_online_by_openid(client, db):
    """未绑微信 → can_pay_online=false；绑了 → true（小程序据此隐藏/显示支付入口）。"""
    _parent, child, mh = _family(client, phone="13800009981")
    r = client.get(f"/api/miniapp/payment/plans?child_id={child['id']}", headers=mh)
    assert r.status_code == 200, r.text
    assert r.json()["can_pay_online"] is False
    _bind_openid(db, _parent["id"], openid="o_test_9981")
    r2 = client.get(f"/api/miniapp/payment/plans?child_id={child['id']}", headers=mh)
    assert r2.json()["can_pay_online"] is True


def test_login_payload_carries_wechat_bound(client, db):
    """登录载荷带 wechat_bound（前端不猜：服务端说了算）。"""
    parent, _child, mh = _family(client, phone="13800009982")
    login = client.post("/api/miniapp/login", json={"phone": "13800009982", "code": "1234"})
    assert login.json()["parent"]["wechat_bound"] is False
    _bind_openid(db, parent["id"], openid="o_test_9982")
    login2 = client.post("/api/miniapp/login", json={"phone": "13800009982", "code": "1234"})
    assert login2.json()["parent"]["wechat_bound"] is True


def test_prepay_without_openid_returns_422_with_guidance(client, db):
    """未绑微信发起支付 → 422 + 明确指引（前端已隐藏入口，这是服务端兜底）。"""
    _parent, child, mh = _family(client, phone="13800009983")
    created = _online_order(client, mh, child["id"])
    r = client.post(f"/api/miniapp/orders/{created['id']}/pay", headers=mh)
    assert r.status_code == 422, r.text
    assert "微信一键登录" in r.json()["detail"]


# ---------- P0-4 短信原子校验 ----------


def test_sms_concurrent_wrong_codes_cannot_exceed_limit(client, db):
    """20 个并发错误验证码 → attempts 打满上限，且超限请求全部被拒（丢失更新已修）。"""
    from backend.database import SessionLocal
    from backend.domain.identity.sms_service import SmsCodeService

    phone = "13800009991"
    _parent_with_child(client, phone)
    _send_and_read_code(client, phone)

    def _try_wrong(_i):
        session = SessionLocal()
        try:
            SmsCodeService(session).verify(phone, "000000", SmsCode.PURPOSE_LOGIN)
            return "ok"
        except Exception as exc:  # ValidationError（业务拒绝）都从这里出
            return str(exc)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_try_wrong, range(20)))

    row = db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.id.desc()).first()
    db.refresh(row)
    assert row.attempts == 5, f"attempts 必须打满上限（丢失更新会只记到 3-4）：{row.attempts}"
    too_many = [r for r in results if "错误次数过多" in r]
    assert len(too_many) == 16, f"20 次里只应放行 4 次（第 5 次即达上限）：{sorted(results)}"
    assert not [r for r in results if r == "ok"]


def test_sms_correct_code_consumed_at_most_once(client, db):
    """同一正确码并发提交 → 只有一个能消费（一次性 + 防并发重放）。"""
    from backend.database import SessionLocal
    from backend.domain.identity.sms_service import SmsCodeService

    phone = "13800009992"
    _parent_with_child(client, phone)
    real = _send_and_read_code(client, phone)

    def _try_correct(_i):
        session = SessionLocal()
        try:
            SmsCodeService(session).verify(phone, real, SmsCode.PURPOSE_LOGIN)
            return "ok"
        except Exception as exc:
            return str(exc)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_try_correct, range(6)))
    assert results.count("ok") == 1, results
    row = db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.id.desc()).first()
    db.refresh(row)
    assert row.used_at is not None


# ---------- P1-5 限流 XFF ----------


def test_forged_xff_cannot_bypass_login_rate_limit(client):
    """伪造 X-Forwarded-For 不换限流桶（修复前每个伪造 IP 一个新桶，3/60 形同虚设）。"""
    from backend.middleware.rate_limit import _limiter

    _limiter._requests.clear()
    phone = "13800009993"
    _parent_with_child(client, phone)
    for i in range(3):
        client.post(
            "/api/miniapp/login",
            json={"phone": phone, "code": "000000"},
            headers={"X-Forwarded-For": f"10.9.1.{i}"},
        )
    r = client.post(
        "/api/miniapp/login",
        json={"phone": phone, "code": "000000"},
        headers={"X-Forwarded-For": "10.9.1.99"},
    )
    assert r.status_code == 429, f"伪造 XFF 不该绕过限流：{r.status_code} {r.text[:80]}"


def test_client_ip_reads_xff_only_when_trusted_proxy_configured(monkeypatch):
    """TRUSTED_PROXY_COUNT=0 不读 XFF；=1 取右起第 1 段（伪造的左侧段不采信）。"""
    import types

    from backend.config import get_settings
    from backend.middleware.rate_limit import _client_ip

    fake = types.SimpleNamespace(
        headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
        client=types.SimpleNamespace(host="9.9.9.9"),
    )
    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_COUNT", 0)
    assert _client_ip(fake) == "9.9.9.9"
    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_COUNT", 1)
    assert _client_ip(fake) == "5.6.7.8"
    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_COUNT", 2)
    assert _client_ip(fake) == "1.2.3.4"


# ---------- P1-6 生产校验 ----------


def test_production_validation_not_bypassed_by_debug_flag():
    """APP_ENV=production + DEBUG=true → 仍拦 mock 支付通道（一个变量关不掉全部红线）。"""
    from backend.config import Settings

    prod = Settings(APP_ENV="production", DEBUG=True, PAYMENT_PROVIDER="mock")
    with pytest.raises(RuntimeError) as exc:
        prod.validate_production()
    assert "PAYMENT_PROVIDER" in str(exc.value)
    # dev 态照旧跳过（本地开发不该被生产校验挡住）
    Settings(APP_ENV="dev", DEBUG=True, PAYMENT_PROVIDER="mock").validate_production()


# ---------- P1-7 退款回调金额 ----------


def test_refund_callback_amount_mismatch_rejected(client, db, monkeypatch):
    """退款回调金额 ≠ 申请金额 → 不落终态（修复前照抄回调把整单记成已退款）。"""
    # 恒回 PROCESSING 的桩：退款单留在"执行中"等退款结果通知（验签/解密仍走 mock）
    _fail_gateway(monkeypatch, fail_times=0, refund_state="PROCESSING")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009994")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING
    out_refund_no = req.out_refund_no

    bad = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": out_refund_no, "refund_status": "SUCCESS", "amount": "10.00"},
        headers=h,
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["http_status"] == 400, bad.json()
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING, "金额不符不得落终态"

    good = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": out_refund_no, "refund_status": "SUCCESS"},
        headers=h,
    )
    assert good.json()["http_status"] == 200, good.json()
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_REFUNDED


# ---------- P2-8 列表 SQL 条数 ----------


def test_refund_admin_list_sql_count_is_constant(client, db):
    """退款台账每请求 SQL 条数与行数无关（修复前 1+2N，200 行约 401 条）。"""
    from backend.database import SessionLocal, engine
    from backend.domain.identity import refund_queries

    parent, child, mh, order = _online_paid_order(client, db, phone="13800009995")

    def _count_statements() -> int:
        session = SessionLocal()
        statements: list[str] = []

        def _rec(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _rec)
        try:
            rows = refund_queries.admin_list(session)
            assert rows
        finally:
            event.remove(engine, "before_cursor_execute", _rec)
            session.close()
        return len(statements)

    db.add(
        RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order.id,
            child_id=child["id"],
            amount=Decimal("1.00"),
            reason="造单 order",
            status=RefundRequest.STATUS_PENDING,
        )
    )
    db.add(
        RefundRequest(
            kind=RefundRequest.KIND_DEPOSIT,
            deposit_id=1,
            child_id=child["id"],
            amount=Decimal("1.00"),
            reason="造单 deposit 0",
            status=RefundRequest.STATUS_PENDING,
        )
    )
    db.commit()
    two_rows = _count_statements()
    for i in range(6):
        db.add(
            RefundRequest(
                kind=RefundRequest.KIND_DEPOSIT,
                deposit_id=1,
                child_id=child["id"],
                amount=Decimal("1.00"),
                reason=f"造单 deposit {i}",
                status=RefundRequest.STATUS_PENDING,
            )
        )
    db.commit()
    eight_rows = _count_statements()
    assert eight_rows == two_rows, f"SQL 条数必须常数级：2 行={two_rows} 8 行={eight_rows}"


# ---------- P2-11 对账读权限 ----------


def test_staff_can_read_reconciliations_but_not_run(client, staff_headers):
    """专员可读报告（dashboard.view，与后端同码）；跑一轮仍超管专属。"""
    r = client.get("/api/admin/payments/reconciliations", headers=staff_headers)
    assert r.status_code == 200, r.text
    assert "items" in r.json()
    run = client.post("/api/admin/payments/reconcile", json={}, headers=staff_headers)
    assert run.status_code == 403, run.text


# ---------- P2-13 / P2-14 / P2-15 / P3-19 回调健壮性 ----------


def test_bind_wechat_integrity_error_maps_to_422(client, db, monkeypatch):
    """绑定并发穿透（唯一索引）→ 422 业务冲突，不是 500 堆栈。"""
    from backend.domain.identity.auth import make_bind_ticket
    from backend.domain.identity.models import Parent
    from backend.domain.identity.sms_service import hash_code

    a, _c, _h2 = _family(client, phone="13800009996")
    b, _c2, _h3 = _family(client, phone="13800009997")
    taken = "o_test_taken_9996"
    _bind_openid(db, a["id"], openid=taken)
    # 绕过"该微信已绑定其他手机号"的前置检查，直达唯一索引冲突路径（模拟并发穿透）
    monkeypatch.setattr(
        "backend.domain.identity.sms_service.parent_by_openid", lambda _db, _openid: None
    )
    db.add(
        SmsCode(
            phone="13800009997",
            purpose=SmsCode.PURPOSE_BIND,
            code_hash=hash_code("13800009997", "654321"),
            expires_at=datetime.now() + timedelta(minutes=5),
        )
    )
    db.commit()
    r = client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": make_bind_ticket(taken), "phone": "13800009997", "code": "654321"},
    )
    assert r.status_code == 422, f"应是业务 422（唯一索引冲突），实 {r.status_code}: {r.text[:120]}"
    db.expire_all()
    row_b = db.query(Parent).filter(Parent.id == b["id"]).first()
    db.refresh(row_b)
    assert not (row_b.wechat_openid or "").strip(), "失败后不得留下半绑定状态"


def test_refund_notify_for_non_processable_state_returns_200(client, db):
    """退款单状态不可推进（如 pending）收到退款通知 → 200 忽略 + 审计（不是 500 让微信重发 24h）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009998")
    req = RefundRequest(
        kind=RefundRequest.KIND_ORDER,
        order_id=order.id,
        child_id=child["id"],
        amount=order.amount,
        reason="造单：待审核",
        status=RefundRequest.STATUS_PENDING,
    )
    db.add(req)
    db.flush()
    req.out_refund_no = f"RF{req.id}-1-1"
    db.commit()
    h = _h(client)
    r = client.post(
        "/api/admin/payments/simulate-refund-callback",
        json={"out_refund_no": req.out_refund_no, "refund_status": "SUCCESS"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["http_status"] == 200, r.json()
    assert "忽略" in r.json()["response"]["message"]
    row = _refund_row(db, req.id)
    assert row.status == RefundRequest.STATUS_PENDING, "非法状态的单不得被通知改状态"


def test_pay_callback_integrity_error_is_audited_not_500(client, db, monkeypatch):
    """并发入账被唯一索引拦下 → 200 + 审计（不抛 500 触发微信重试风暴）。"""
    from backend.domain.identity.order_service import OrderService

    _parent, child, mh = _family(client, phone="13800009999")
    _bind_openid(db, _parent["id"], openid="o_test_9999")
    created = _online_order(client, mh, child["id"])

    def _boom(self, order, **kwargs):
        raise IntegrityError("UPDATE orders", {}, Exception("uq_order_transaction"))

    monkeypatch.setattr(OrderService, "_settle_paid", _boom)
    r = _notify(client, created["order_no"], "500.00", transaction_id="txn_dup_9999")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "已记录待人工处理"
    row = db.query(Order).filter(Order.order_no == created["order_no"]).first()
    db.refresh(row)
    assert row.status == Order.STATUS_PENDING_PAYMENT


def test_simulate_callback_bad_amount_is_422(client, db):
    """演练端点金额格式错 → 422（修复前 Decimal 抛 InvalidOperation → 500）。"""
    _parent, child, mh = _family(client, phone="13800009901")
    _bind_openid(db, _parent["id"], openid="o_test_9901")
    created = _online_order(client, mh, child["id"])
    h = _h(client)
    r = client.post(
        "/api/admin/payments/simulate-callback",
        json={"order_no": created["order_no"], "amount": "abc"},
        headers=h,
    )
    assert r.status_code == 422, f"{r.status_code} {r.text[:120]}"
