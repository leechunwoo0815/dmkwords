# tests/unit/test_pm_decisions_20261012.py — 拍板批次回归（2026-10-12 第四十九轮）
"""守 12 项产品口径 + 技术项（回退源码即红）。口径出处：PRD §2.2/§3.5/§3.6/§4.5/§9.3/§11.3
+ `docs/09` 第四十九轮任务包。

覆盖：
P1 短信关闭=拒启 / P1b 登录选项开关 / P2 单 IP 日上限 / P3 改号撤 token / P4 活动取消作废订单 /
P5 在借副本禁改状态 + 报损 / P6 下架禁借禁预约 / P7 转让后年费 / P8 混合押金不自动原路退 /
P9 冻结期禁收款（三处）/ P10 资金异常进待办 + 任务连续失败计数 /
T2 退款通知单号校验 / T3 支付不复活已取消单 / T4 管理端押金闸 / T8 删书拦预约 / T11 入参上界
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from backend.common.exceptions import ConflictError
from backend.domain.identity.models import Order, RefundRequest
from tests.unit.test_a1_closure_20261009 import _admin_order
from tests.unit.test_wm10_concurrency import _family, _h

# ---------- P1 / P1b 短信开关 ----------


def test_production_allows_sms_disabled():
    """2026-10-29 甲方裁定：短信退役（全部微信一键登录）→ 生产允许关闭短信。

    关闭时的安全性由 `/sms/send` 与 `SmsCodeService.verify` 的 fail-closed 保证
    （见 `test_login_wechat_sms.py::test_sms_disabled_fails_closed`）。
    """
    from backend.config import Settings

    Settings(
        DEBUG=False,
        SECRET_KEY="real-secret-0123456789abcdef0123456789",
        DB_PASSWORD="pw",
        WECHAT_APP_ID="wx",
        WECHAT_APP_SECRET="sk",
        LOGIN_DEV_CODE="",
        CORS_ORIGINS="https://admin.example.com",
        PAYMENT_ENABLED=False,
        SMS_ENABLED=False,
        SMS_PROVIDER="mock",
    ).validate_production()


def test_login_options_exposes_sms_switch(client):
    """登录选项端点公开布尔开关（小程序据此隐藏手机号入口）。"""
    r = client.get("/api/miniapp/login/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("sms_enabled"), bool)
    assert body.get("wechat_enabled") is True


# ---------- P2 单 IP 日上限 ----------


def test_sms_ip_daily_limit_blocks_same_ip(client, db):
    """同一 IP 当日发码达上限 → 拒绝（修复前只按手机号计额度，换号即可刷爆他人额度）。"""
    from backend.domain.identity.sms_service import SmsCodeService

    # 把 IP 上限压到 2，便于用例内触发
    db.execute(
        text("UPDATE system_configs SET config_value='2' WHERE config_key='sms_ip_daily_limit'")
    )
    db.commit()
    ip = "203.0.113.77"
    svc = SmsCodeService(db)
    sent = 0
    for i in range(4):
        try:
            svc.send(f"138000077{i:02d}", client_ip=ip)
            sent += 1
        except ConflictError:
            pass
    assert sent == 2, f"同 IP 只应放行 2 条，实测 {sent}"
    rows = db.execute(
        text("SELECT COUNT(*) FROM sms_codes WHERE request_ip=:ip"), {"ip": ip}
    ).scalar()
    assert rows == 2, f"来源 IP 必须落库（否则无法计量）：{rows}"


# ---------- P3 改号撤 token ----------


def test_change_parent_phone_revokes_old_token(client, db):
    """馆员改手机号 → 旧 token 立刻失效（修复前旧登录最长 30 天仍可读写该家庭档案）。"""
    h = _h(client)
    parent, _child, mh = _family(client, h, phone="13800009701")
    assert client.get("/api/miniapp/children", headers=mh).status_code == 200
    r = client.patch(
        f"/api/admin/members/parents/{parent['id']}", json={"phone": "13800009702"}, headers=h
    )
    assert r.status_code == 200, r.text
    after = client.get("/api/miniapp/children", headers=mh)
    assert after.status_code == 401, f"旧 token 必须失效：{after.status_code}"


# ---------- P4 活动取消作废未收款订单 ----------


def test_cancel_activity_voids_unpaid_orders(client, db):
    """活动取消 → 关联的待收款订单一并作废（修复前 48h 内还能被收银台收钱）。"""
    h = _h(client)
    _p, child, _mh = _family(client, h, phone="13800009703")
    paid_act = client.post(
        "/api/admin/activities",
        json={
            "title": "P4 收费活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=2)).isoformat(),
            "location": "门店",
            "fee": "99.00",
            "max_quota": 10,
        },
        headers=h,
    ).json()
    assert paid_act.get("id"), paid_act
    order = client.post(
        "/api/admin/orders",
        json={
            "order_type": "activity_fee",
            "child_id": child["id"],
            "amount": "99.00",
            "activity_id": paid_act["id"],
            "remark": "P4 活动单",
        },
        headers=h,
    ).json()
    assert order.get("id"), order
    enr_id = db.execute(text("SELECT MAX(id) FROM activity_enrollments")).scalar()
    db.execute(
        text(
            "UPDATE activity_enrollments SET activity_id=:a, status='pending_payment', order_id=:o "
            "WHERE id=:e"
        ),
        {"a": paid_act["id"], "o": order["id"], "e": enr_id},
    )
    db.commit()
    r = client.post(f"/api/admin/activities/{paid_act['id']}/cancel", json={}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("orders_voided", 0) >= 1, body
    db.expire_all()
    row = db.query(Order).filter(Order.id == order["id"]).first()
    assert row.status == Order.STATUS_CANCELLED, row.status


# ---------- P5 在借副本禁改状态 + 报损 ----------


def test_borrowed_copy_cannot_change_status_but_can_report_damage(client, db):
    """在借副本直接改状态被拒；「报损」可登记并生成赔偿待办（不动在借记录）。"""
    from backend.common.admin_notification_models import AdminNotification

    h = _h(client)
    _p, child, mh = _family(client, h, phone="13800009704")
    from tests.unit.test_wm10_concurrency import _pay, _pay_deposit

    _pay(client, h, child["id"], "observation_fee")
    _pay_deposit(client, h, child["id"])
    from tests.unit.helpers import force_book_on

    book = client.post(
        "/api/admin/books",
        json={"isbn": "9780000099704", "title": "P5 在借副本用例", "word_count": 100},
        headers=h,
    ).json()
    force_book_on(client, h, book["id"])
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    copy_id = (copies["items"] if isinstance(copies, dict) else copies)[0]["id"]
    br = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": child["id"], "copy_id": copy_id},
        headers=h,
    )
    assert br.status_code == 200, br.text
    r = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "available", "reason": "手滑"},
        headers=h,
    )
    assert r.status_code == 409, f"在借副本不该能直接改状态：{r.status_code} {r.text[:120]}"
    r2 = client.post(
        f"/api/admin/copies/{copy_id}/report-damage",
        json={"kind": "lost", "reason": "孩子说书丢了"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    n = (
        db.query(AdminNotification)
        .filter(AdminNotification.scene == AdminNotification.SCENE_COPY_DAMAGE_COMPENSATION)
        .count()
    )
    assert n >= 1, "报损必须生成赔偿待办"


# ---------- P6 下架禁借 / 禁预约 ----------


def test_off_shelf_book_cannot_be_borrowed_or_reserved(client, db):
    """下架书禁借（ISBN 与副本两路）且禁预约。"""
    h = _h(client)
    _p, child, mh = _family(client, h, phone="13800009705")
    from tests.unit.test_wm10_concurrency import _pay, _pay_deposit

    _pay(client, h, child["id"], "observation_fee")
    _pay_deposit(client, h, child["id"])
    from tests.unit.helpers import force_book_on

    book = client.post(
        "/api/admin/books",
        json={"isbn": "9780000099705", "title": "P6 下架用例", "word_count": 100},
        headers=h,
    ).json()
    force_book_on(client, h, book["id"])
    client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)  # → off
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": child["id"], "isbn": "9780000099705"},
        headers=h,
    )
    assert r.status_code == 422, f"下架书不该能借：{r.status_code} {r.text[:120]}"
    r2 = client.post(
        "/api/miniapp/reservations",
        json={"child_id": child["id"], "book_id": book["id"]},
        headers=mh,
    )
    assert r2.status_code == 422, f"下架书不该能预约：{r2.status_code} {r2.text[:120]}"


# ---------- P7 转让后年费不再受理退款 ----------


def test_transferred_annual_fee_cannot_be_refunded(client, db):
    """转让完成后年费订单标记「已随转让处置」，退款申请被拒。"""
    h = _h(client)
    _p, child, mh = _family(client, h, phone="13800009706")
    order = _admin_order(client, h, child["id"], order_type="formal_fee", amount="6000.00")
    client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "P7 年费"},
        headers=h,
    )
    db.expire_all()
    row = db.query(Order).filter(Order.id == order["id"]).first()
    assert row.status == Order.STATUS_PAID
    row.refund_status = Order.REFUND_STATUS_TRANSFERRED  # 等价于转让完成时的落标记
    db.commit()
    r = client.post(
        "/api/miniapp/refund-requests",
        json={"child_id": child["id"], "order_id": order["id"], "reason": "想退年费"},
        headers=mh,
    )
    assert r.status_code == 422, f"已随转让处置的年费不该受理退款：{r.status_code} {r.text[:120]}"
    assert "转让" in r.json()["detail"]


# ---------- P8 混合构成的押金不自动原路退 ----------


def test_mixed_deposit_is_not_auto_refunded_via_gateway(client, db):
    """线上那笔金额小于应退金额 → 不提交网关，明确提示人工线下退款。"""
    from backend.domain.identity import refund_online
    from backend.domain.identity.models import Child, RefundRequest

    h = _h(client)
    _p, child_row, _mh = _family(client, h, phone="13800009707")
    child = db.query(Child).filter(Child.id == child_row["id"]).first()
    db.add(
        Order(
            order_no="DMKP8MIXED0001",
            order_type=Order.TYPE_DEPOSIT_SUPPLEMENT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=Decimal("200.00"),
            status=Order.STATUS_PAID,
            pay_method="wechat",
            transaction_id="txn_p8_mixed",
            paid_at=datetime.now(),
        )
    )
    db.commit()
    from backend.domain.billing.models import Deposit

    dep = Deposit(
        child_id=child.id,
        amount=Decimal("1400.00"),
        available_amount=Decimal("1400.00"),
        deducted_amount=Decimal("0.00"),
        unpaid_balance=Decimal("0.00"),
        status=Deposit.STATUS_REFUNDING,
    )
    db.add(dep)
    db.commit()
    req = RefundRequest(
        kind=RefundRequest.KIND_DEPOSIT,
        deposit_id=dep.id,
        child_id=child.id,
        amount=Decimal("1200.00"),
        reason="P8 混合押金",
        status=RefundRequest.STATUS_APPROVED,
    )
    db.add(req)
    db.commit()
    order, note = refund_online.resolve_online_source(db, req)
    assert order is None, "混合构成的押金不该自动原路退"
    assert "人工线下退款" in note or "混合" in note, note


# ---------- P9 冻结期禁收款（三处）----------


def test_frozen_child_cannot_be_collected(client, db):
    """冻结（退会/转让审核中）→ 确认收款、发起支付、押金补缴三处都被拒。"""
    h = _h(client)
    _p, child, mh = _family(client, h, phone="13800009708")
    from tests.unit.test_wm10_concurrency import _pay

    _pay(client, h, child["id"], "observation_fee")  # 先成为会员（否则前置闸门先报"入会员后可用"）
    order = _admin_order(client, h, child["id"], order_type="formal_fee", amount="6000.00")
    db.execute(text("UPDATE children SET operation_locked=1 WHERE id=:i"), {"i": child["id"]})
    db.commit()
    r = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "冻结期收款"},
        headers=h,
    )
    assert r.status_code == 422 and "冻结" in r.json()["detail"], r.text[:160]
    r2 = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh)
    assert r2.status_code == 422 and "冻结" in r2.json()["detail"], r2.text[:160]
    r3 = client.post(
        "/api/miniapp/deposits/supplement-orders", json={"child_id": child["id"]}, headers=mh
    )
    assert r3.status_code == 422 and "冻结" in r3.json()["detail"], r3.text[:160]


# ---------- P10 可观测性 ----------


def test_payment_anomaly_creates_admin_todo(client, db):
    """资金异常留痕的同时进管理端待办（修复前只有审计行，仅超管可见、无人会看到）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.identity.payment_service import PaymentService

    h = _h(client)
    _p, child, _mh = _family(client, h, phone="13800009709")
    order = _admin_order(client, h, child["id"], order_type="custom", amount="10.00")
    db.expire_all()
    PaymentService(db)._audit_anomaly("测试资金异常", target_id=order["order_no"], reason="P10")
    db.commit()
    n = (
        db.query(AdminNotification)
        .filter(AdminNotification.scene == AdminNotification.SCENE_PAYMENT_ANOMALY)
        .count()
    )
    assert n >= 1, "资金异常必须进待办"


def test_task_specs_expose_consecutive_failures(client, db):
    """任务看板暴露"连续失败"次数（一次成功不该把连续失败洗白）。"""
    from backend.common.notification_models import TaskRunLog

    h = _h(client)
    name = client.get("/api/admin/tasks", headers=h).json()["items"][0]["name"]
    for _ in range(3):
        db.add(TaskRunLog(task_name=name, status=TaskRunLog.STATUS_FAILED, error="boom"))
    db.commit()
    items = client.get("/api/admin/tasks", headers=h).json()["items"]
    row = next(i for i in items if i["name"] == name)
    assert row.get("consecutive_failures", 0) >= 3, row


# ---------- T2 / T3 / T4 / T8 / T11 ----------


def test_refund_notify_with_stale_out_refund_no_is_ignored(client, db, monkeypatch):
    """旧号（换号重试前那笔）的迟到通知必须被忽略，不得落到当前在途那笔上。

    **本用例重写（第八轮审查 P2-65）**：原先 POST 到 `/api/pay/wechat/refund-notify`
    ——该端点**全仓没有路由**（真实入口是 `/api/pay/wechat/notify`，按 `event_type` 分派），
    404 天然满足"单号没被改写"，等于 `_handle_refund_notify` 的防旧号判据**零守护**。
    现在**直接调 `_handle_refund_notify`**（只桩掉 `_decrypt`，其余全走真实判据），
    并断言三件事：① 旧号通知被忽略且落审计；② 当前单号未被改写；③ **对照组**——
    当前单号的 SUCCESS 通知必须真的落终态（不许修成"一律忽略"）。
    """
    from backend.common.gateways.payment.types import PaymentCallbackData
    from backend.common.system_models import AuditLog
    from backend.domain.identity.payment_service import PaymentService
    from backend.domain.identity.wm10_service import RefundService
    from tests.unit.test_wm12_refund_reconcile import (
        _apply_refund,
        _approve,
        _execute,
        _online_paid_order,
        _refund_row,
        _stub_gateway,
    )

    _stub_gateway(monkeypatch, state="PROCESSING", refund_id="wx_p8")
    _parent, child, mh, order = _online_paid_order(client, db, phone="13800009710")
    applied = _apply_refund(client, mh, order, child["id"])
    h = _h(client)
    _approve(client, h, applied["id"])
    _execute(client, h, applied["id"], remark="原路退")
    req = _refund_row(db, applied["id"])
    assert req.status == RefundRequest.STATUS_PROCESSING
    current = req.out_refund_no
    assert current, "前置失败：执行后应有商户退款单号"
    stale = current.replace("-1-1", "-2-1") if "-1-1" in current else "RF999999-1-1"
    amount = req.amount

    def _notify(out_refund_no: str):
        monkeypatch.setattr(
            PaymentService,
            "_decrypt",
            staticmethod(
                lambda gateway, envelope: PaymentCallbackData(
                    out_trade_no=order["order_no"] if isinstance(order, dict) else order.order_no,
                    out_refund_no=out_refund_no,
                    refund_status="SUCCESS",
                    refund_amount=amount,
                )
            ),
        )
        return PaymentService(db)._handle_refund_notify(
            gateway=None, envelope={}, event_type="REFUND.SUCCESS"
        )

    # ① 旧号迟到通知：忽略（200 + 明确文案），当前单号不动
    code, body = _notify(stale)
    assert code == 200 and "已忽略" in body.get("message", ""), (code, body)
    db.expire_all()
    assert _refund_row(db, applied["id"]).out_refund_no == current, "当前提交的单号不得被改写"
    assert _refund_row(db, applied["id"]).status == RefundRequest.STATUS_PROCESSING, (
        "在途单不得被旧号通知推进终态"
    )
    # 审计必须留痕（否则"被忽略了"与"根本没收到"分不清）
    hit = (
        db.query(AuditLog)
        .filter(AuditLog.detail.like("%单号与当前提交不一致%"))
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert hit is not None, "旧号通知被忽略必须留审计（排查时能看见）"

    # ② 对照组：**当前单号**的 SUCCESS 通知必须真的落终态（防"一律忽略"式过修）
    code2, body2 = _notify(current)
    assert code2 == 200 and "已忽略" not in body2.get("message", ""), (code2, body2)
    db.expire_all()
    assert _refund_row(db, applied["id"]).status == RefundRequest.STATUS_REFUNDED, (
        "当前提交那笔的成功回调必须落已退款"
    )
    assert RefundService  # 引用保留（下文若有状态机断言可复用）


def test_prepay_does_not_revive_cancelled_order(client, db):
    """已取消的订单不能被"去支付"复活（修复前无条件 ORM 赋值会覆盖 cancelled）。"""
    h = _h(client)
    _p, child, mh = _family(client, h, phone="13800009711")
    order = _admin_order(client, h, child["id"], order_type="custom", amount="10.00")
    db.execute(text("UPDATE orders SET status='cancelled' WHERE id=:i"), {"i": order["id"]})
    db.commit()
    r = client.post(f"/api/miniapp/orders/{order['id']}/pay", headers=mh)
    assert r.status_code == 422, f"已取消订单不该能发起支付：{r.status_code}"
    db.expire_all()
    row = db.query(Order).filter(Order.id == order["id"]).first()
    assert row.status == Order.STATUS_CANCELLED, f"状态被复活了：{row.status}"


def test_admin_deposit_order_rejected_when_not_unpaid(client, db):
    """押金在"部分扣除/退款中"时，管理端也不许再收一笔全额（与家长端同款闸门）。"""
    from backend.domain.billing.models import Deposit

    h = _h(client)
    _p, child_row, _mh = _family(client, h, phone="13800009712")
    db.add(
        Deposit(
            child_id=child_row["id"],
            amount=Decimal("1200.00"),
            available_amount=Decimal("700.00"),
            deducted_amount=Decimal("500.00"),
            unpaid_balance=Decimal("0.00"),
            status=Deposit.STATUS_PARTIALLY_DEDUCTED,
        )
    )
    db.commit()
    r = client.post(f"/api/admin/deposits/children/{child_row['id']}/orders", json={}, headers=h)
    assert r.status_code == 422, f"部分扣除状态不该能再建押金单：{r.status_code} {r.text[:120]}"


def test_delete_book_blocked_by_active_reservation(client, db):
    """有活跃预约的书不许删（修复前只拦借出中，删完预约成孤儿）。"""
    from backend.domain.reading.models import Reservation

    h = _h(client)
    _p, child_row, mh = _family(client, h, phone="13800009713")
    from tests.unit.test_wm10_concurrency import _pay, _pay_deposit

    _pay(client, h, child_row["id"], "observation_fee")  # 预约要求有效会员
    _pay_deposit(client, h, child_row["id"])  # 且押金已缴
    from tests.unit.helpers import force_book_on

    book = client.post(
        "/api/admin/books",
        json={"isbn": "9780000099713", "title": "T8 预约用例", "word_count": 100},
        headers=h,
    ).json()
    force_book_on(client, h, book["id"])
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    copy_id = (copies["items"] if isinstance(copies, dict) else copies)[0]["id"]
    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": child_row["id"], "book_id": book["id"]},
        headers=mh,
    )
    assert r.status_code == 200, r.text
    assert (
        db.query(Reservation)
        .filter(Reservation.book_id == book["id"], Reservation.status == Reservation.STATUS_ACTIVE)
        .count()
        >= 1
    )
    _ = copy_id
    d = client.delete(f"/api/admin/books/{book['id']}", headers=h)
    assert d.status_code == 409, f"有预约的书不该能删：{d.status_code} {d.text[:120]}"


def test_activity_fee_over_limit_is_422(client, db):
    """超大金额 → 422 参数错（修复前严格模式溢出 500）。"""
    h = _h(client)
    _p, child, _mh = _family(client, h, phone="13800009714")
    r = client.post(
        "/api/admin/activities",
        json={
            "title": "T11 巨款活动",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=3)).isoformat(),
            "location": "门店",
            "fee": "99999999999.99",
            "max_quota": 5,
        },
        headers=h,
    )
    assert r.status_code == 422, f"超出列精度应 422：{r.status_code} {r.text[:120]}"
    _ = child
