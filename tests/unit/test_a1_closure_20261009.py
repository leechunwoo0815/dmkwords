# tests/unit/test_a1_closure_20261009.py — 上线前闭环 A1（2026-10-09，11 项）
"""A1 组（"不需要甲方资料"的上线前必须项）逐条回归。每条都钉住"缺陷语义"：

① 回调未预期异常 → 兜底 200 + anomaly（原：ValidationError 逃逸成 422 → 微信重发 24h）
② 短信网关缺 SDK/凭据 → fail-closed（原：假成功 + 日志打验证码前 4 位）
③ `order.cancel` 条件 UPDATE（原：30 轮并发 27 轮"终态 cancelled 却 paid_at 置位"）
④ `checkins`/`reading_progress` 唯一索引（原：6 线程并发打卡落 6 行）
⑤ 账单比对只比线上单（原：线下现金单全被记 local_only → 真差异被淹没）
⑥ 退款回调缺金额 → 400（原：只审计后继续按全额落已退）
⑦ seed 守卫改用 APP_ENV（原：DEBUG 开关 → 生产可播种弱口令超管）
⑧ 音频/Excel 上传体积上限（原：无上限、全量读进内存）
⑨ admin-web 超管按钮权限门（原：专员点了必 403）
⑩ 订单页「去支付」看服务端开关（原：纯人工版本显示死按钮）
⑪ 门禁新增接线检查器（bind handler 存在性 / 插值类名可解析）
"""

from __future__ import annotations

import asyncio
import subprocess
import types
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.domain.identity.models import Order
from backend.domain.identity.payment_reconcile_service import PaymentReconcileService
from tests.unit.test_wm12_payment import _family, _h
from tests.unit.test_wm12_refund_reconcile import (
    _apply_refund,
    _approve,
    _online_paid_order,
    _refund_row,
)

ROOT = Path(__file__).resolve().parents[2]
PROD_BASE = dict(
    DEBUG=False,
    SECRET_KEY="real-secret",
    DB_PASSWORD="pw",
    WECHAT_APP_ID="wx",
    WECHAT_APP_SECRET="sk",
    LOGIN_DEV_CODE="",
    CORS_ORIGINS="https://admin.example.com",
    PAYMENT_ENABLED=False,
)


def _admin_order(client, h, child_id: int, order_type="custom", amount="1.00"):
    r = client.post(
        "/api/admin/orders",
        json={
            "order_type": order_type,
            "child_id": child_id,
            "amount": amount,
            "remark": "A1 造单",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- ① 回调兜底 200 ----------


def test_pay_notify_unexpected_exception_returns_200_and_audit(client, db, monkeypatch):
    """结算链抛未预期异常 → 回调仍 200「已记录待人工处理」+ anomaly（不是 422 让微信重发）。"""
    h = _h(client)
    _parent, child, _mh = _family(client, phone="13800009941")
    order = _admin_order(client, h, child["id"])

    from backend.common.exceptions import ValidationError
    from backend.domain.identity.order_service import OrderService

    def boom(self, order, **kwargs):  # noqa: ARG001 —— 模拟真实触发（重复缴押金的 ValidationError）
        raise ValidationError("押金已缴纳，请勿重复")

    monkeypatch.setattr(OrderService, "_settle_paid", boom)
    r = client.post(
        "/api/admin/payments/simulate-callback", json={"order_no": order["order_no"]}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["response"]["message"] == "已记录待人工处理"

    row = db.query(Order).filter(Order.order_no == order["order_no"]).first()
    db.refresh(row)
    assert row.status != Order.STATUS_PAID

    from backend.domain.admin.models import AuditLog

    anomalies = (
        db.query(AuditLog)
        .filter(AuditLog.action == "payment.anomaly", AuditLog.target_id == order["order_no"])
        .count()
    )
    assert anomalies >= 1, "兜底路径必须留痕（钱收了没入账，不能无痕）"


# ---------- ② 短信 fail-closed ----------


def test_sms_gateway_fails_closed_without_sdk(caplog):
    """缺 SDK/凭据 → success=False（绝不假成功），且日志里不出现验证码任何位。"""
    from backend.config import sms_sdk_available
    from backend.integrations.sms.aliyun import AliyunSmsGateway

    if sms_sdk_available("aliyun"):
        pytest.skip("本环境已装阿里云 SDK（dev 环境本就不装，跳过）")
    gw = AliyunSmsGateway(app_id="", app_key="", sign_name="", template_code="")
    with caplog.at_level("INFO"):
        res = asyncio.run(gw.send_code("13800001111"))
    assert res.success is False
    assert "不可用" in res.error_message
    assert "验证码" not in caplog.text, "日志不得出现验证码字样/数字"


def test_production_rejects_missing_sms_sdk():
    """生产校验：凭据齐但 SDK 未装 → 拒启（原实现只查凭据非空）。"""
    from backend.config import Settings, sms_sdk_available

    if sms_sdk_available("aliyun"):
        pytest.skip("本环境已装 SDK")
    with pytest.raises(RuntimeError) as exc:
        Settings(
            SMS_ENABLED=True,
            SMS_PROVIDER="aliyun",
            SMS_APP_ID="a",
            SMS_APP_KEY="b",
            SMS_SIGN_NAME="c",
            **PROD_BASE,
        ).validate_production()
    assert "SDK" in str(exc.value)


# ---------- ③ order.cancel 条件 UPDATE ----------


def test_cancel_rejects_order_that_was_paid_concurrently(client, db):
    """钱已收（paid_at 置位）但状态还没翻 → 取消必须被拒（条件 UPDATE 影响行数=0）。"""
    h = _h(client)
    _parent, child, _mh = _family(client, phone="13800009942")
    order = _admin_order(client, h, child["id"])

    # 模拟"确认收款"先落地了钱（paid_at）而状态位尚未可见的窗口（真实交错见 A-9 探针）
    db.execute(
        text("UPDATE orders SET paid_at = :t WHERE id = :i"),
        {"t": datetime.now(), "i": order["id"]},
    )
    db.commit()

    r = client.post(f"/api/admin/orders/{order['id']}/cancel", headers=h)
    assert r.status_code == 422, r.text
    assert "已收款" in r.json()["detail"]
    row = db.query(Order).filter(Order.id == order["id"]).first()
    db.refresh(row)
    assert row.status != Order.STATUS_CANCELLED


# ---------- ④ 唯一索引 ----------


def test_checkin_and_progress_unique_indexes(client, db):
    """库级唯一约束必须挡住并发重复插入（应用层 upsert 是配套，索引是最终防线）。"""
    from backend.domain.reading.models import CheckIn, ReadingProgress

    h = _h(client)
    _parent, child, _mh = _family(client, phone="13800009947")
    book = client.post(
        "/api/admin/books",
        json={"isbn": "9780545582889", "title": "A1 唯一索引书", "word_count": 100},
        headers=h,
    )
    assert book.status_code == 200, book.text
    book_id = book.json()["id"]
    today = date.today()

    db.add(CheckIn(child_id=child["id"], checkin_date=today, book_id=book_id, streak=1))
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(CheckIn(child_id=child["id"], checkin_date=today, book_id=book_id, streak=1))
        db.commit()
    db.rollback()

    db.add(ReadingProgress(child_id=child["id"], book_id=book_id, total_seconds=0))
    db.commit()
    with pytest.raises(IntegrityError):
        db.add(ReadingProgress(child_id=child["id"], book_id=book_id, total_seconds=0))
        db.commit()
    db.rollback()


# ---------- ⑤ 账单只比线上单 ----------


def test_bill_diff_ignores_offline_orders(client, db):
    """线下现金单不属于微信账单核对范围 → 不得被记 `local_only`。"""
    h = _h(client)
    parent, child, mh = _family(client, phone="13800009943")

    offline = _admin_order(client, h, child["id"], order_type="custom", amount="66.00")
    r = client.post(
        f"/api/admin/orders/{offline['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "门店扫码（线下）"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    offline_row = db.query(Order).filter(Order.id == offline["id"]).first()
    db.refresh(offline_row)
    assert offline_row.paid_at is not None and not offline_row.transaction_id

    _parent, _child, _mh2, online = _online_paid_order(client, db, phone="13800009944")
    db.refresh(online)
    day = (online.paid_at or datetime.now()).date()

    header = (
        "交易时间,公众账号ID,商户号,微信订单号,商户订单号,交易状态,订单金额,"
        "商户退款单号,退款金额,退款状态"
    )
    row = f"{day} 10:00:00,wxapp,1900,txn_a,{online.order_no},SUCCESS,{online.amount},,,"
    csv_text = "\n".join([header, row, "总交易单数,1"])

    checked, diffs = PaymentReconcileService(db)._diff_bill(day, csv_text)
    refs = {d["ref"] for d in diffs if d["kind"] == "local_only"}
    assert offline_row.order_no not in refs, "线下单不该进微信账单核对集合"
    assert checked == 1


# ---------- ⑥ 退款回调缺金额即拒 ----------


def test_refund_notify_without_amount_is_rejected(client, db, monkeypatch):
    """缺 `refund_amount` → 400 + 不落终态（与支付侧同口径）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009945")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])

    from backend.common.gateways.payment.types import PaymentCallbackData
    from backend.domain.identity.payment_service import PaymentService

    req = _refund_row(db, applied["id"])
    svc = PaymentService(db)
    monkeypatch.setattr(
        svc,
        "_decrypt",
        lambda gateway, envelope: PaymentCallbackData(
            out_trade_no=order.order_no,
            out_refund_no=req.out_refund_no or f"RF{req.id}-1-1",
            refund_status="SUCCESS",
            refund_amount=None,  # 微信字段改名/部分退款场景
        ),
    )
    code, body = svc._handle_refund_notify(None, {}, "REFUND.SUCCESS")
    assert code == 400 and "金额" in body["message"]
    assert _refund_row(db, applied["id"]).status != "refunded"


# ---------- ⑦ seed 守卫改用 APP_ENV ----------


def test_seed_guard_uses_app_env_not_debug(monkeypatch):
    """生产（APP_ENV=production）拒播种；dev 即使 DEBUG=false 也放行（原实现反着来）。"""
    import backend.config as config_mod
    from backend.seeds import seed_admin

    monkeypatch.setattr(
        config_mod,
        "get_settings",
        lambda: types.SimpleNamespace(APP_ENV="production", DEBUG=True),
    )
    with pytest.raises(RuntimeError) as exc:
        seed_admin.seed()
    assert "生产" in str(exc.value)

    monkeypatch.setattr(
        config_mod,
        "get_settings",
        lambda: types.SimpleNamespace(APP_ENV="dev", DEBUG=False),
    )
    seed_admin.seed()  # dev 库已有账号 → 幂等返回，不抛


# ---------- ⑧ 上传体积上限 ----------


def test_upload_size_limit_for_non_image_uploads():
    from backend.common.exceptions import ValidationError
    from backend.common.file_storage import ensure_upload_size_within_limit

    ok = types.SimpleNamespace(size=1024)
    ensure_upload_size_within_limit(ok, key="audio_upload_max_mb", default_mb=50, label="音频文件")

    big = types.SimpleNamespace(size=51 * 1024 * 1024)
    with pytest.raises(ValidationError) as exc:
        ensure_upload_size_within_limit(
            big, key="audio_upload_max_mb", default_mb=50, label="音频文件"
        )
    assert "音频文件体积超限" in str(exc.value)

    # 配置键已进种子（运营可调）
    from backend.seeds.seed_configs import CONFIG_SEEDS

    keys = {row[0] for row in CONFIG_SEEDS}
    assert {"audio_upload_max_mb", "books_import_max_mb"} <= keys


# ---------- ⑨/⑩ 前端两处（静态断言，防回归） ----------


def test_admin_web_superadmin_buttons_gated():
    growth = (ROOT / "admin-web/src/pages/GrowthManage.tsx").read_text(encoding="utf-8")
    circle = (ROOT / "admin-web/src/pages/CircleManage.tsx").read_text(encoding="utf-8")
    for src, label in ((growth, "GrowthManage"), (circle, "CircleManage")):
        assert 'user?.role === "superadmin"' in src, f"{label} 缺超管判据"
    assert growth.count("isSuperAdmin") >= 4, "GrowthManage 三个按钮都要上门（1 定义 + 3 使用）"


def src_count(text: str, needle: str) -> int:
    return text.count(needle)


def test_miniapp_order_page_respects_payment_switch():
    js = (ROOT / "miniapp/pages/order-pkg/order-history/order-history.js").read_text(
        encoding="utf-8"
    )
    assert "_refreshPayGate" in js and "payment_enabled" in js and "can_pay_online" in js


# ---------- ⑪ 门禁新增检查器 ----------


def test_wiring_checker_passes_and_is_registered():
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/check_miniapp_wiring.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout
    gate = (ROOT / "scripts/gate.sh").read_text(encoding="utf-8")
    assert "check_miniapp_wiring.py" in gate, "检查器必须进门禁"


def test_refund_notify_amount_mismatch_still_rejected(client, db, monkeypatch):
    """对照：金额**不符**（非缺失）仍按原口径 400（本次只改"缺失"分支）。"""
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009946")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])

    from backend.common.gateways.payment.types import PaymentCallbackData
    from backend.domain.identity.payment_service import PaymentService

    req = _refund_row(db, applied["id"])
    svc = PaymentService(db)
    monkeypatch.setattr(
        svc,
        "_decrypt",
        lambda gateway, envelope: PaymentCallbackData(
            out_trade_no=order.order_no,
            out_refund_no=req.out_refund_no or f"RF{req.id}-1-1",
            refund_status="SUCCESS",
            refund_amount=Decimal("0.01"),
        ),
    )
    code, body = svc._handle_refund_notify(None, {}, "REFUND.SUCCESS")
    assert code == 400 and "不符" in body["message"]
