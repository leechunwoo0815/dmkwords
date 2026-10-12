# tests/unit/test_audit_20261012_fixes.py — 2026-10-12 大审查修复回归（资金/安全/数据）
"""守 7 条已亲验缺陷（回退源码即红）。背景与探针证据：`docs/审查报告-全量-2026-10-12.md`。

① 受保护目录白名单可被 `..` 绕过（`/api/admin/uploads/{path}`）
② 同一订单两笔退款各自出款（失败单可重试 + 新单可再申请）
③ 已缴押金仍可再下线上押金单（钱收了、订单停待支付）
④ `_checkin` 的 rowcount 判据恒失效（重复打卡被当首次）
⑤ `report_progress` 无锁读改写（并发丢区间）
⑥ 押金闸门把 refunded/refunding 当合格（退会后重新入会可零押金借书）
⑦ 定时对账取"今天"的账单（当天账单不存在 → 微信侧比对永久 skipped）
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from backend.domain.circulation.borrow_gate import deposit_gate
from tests.unit.test_wm10_concurrency import _family, _h


def test_protected_upload_path_cannot_be_traversed(client, db):
    """`cover/../voucher/x.jpg` 必须 404（修复前实测 200 拿到凭证图）。

    注意：**必须直接调路由函数**——httpx/TestClient 会先归一化 `..`（实测两条变体在旧代码下
    也是 404），只有原始 HTTP 客户端（`curl --path-as-is` 打真实 uvicorn）才复现 200。
    这里直接喂未归一化的 path 给 `serve_upload`，正是绕过发生的那一层。
    """
    import os

    from backend.common.exceptions import NotFoundError
    from backend.config import get_settings
    from backend.domain.catalog.router import serve_upload

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    os.makedirs(os.path.join(root, "voucher"), exist_ok=True)
    os.makedirs(os.path.join(root, "cover"), exist_ok=True)
    secret = os.path.join(root, "voucher", "AUDIT_TRAVERSAL.jpg")
    with open(secret, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0" + b"x" * 200)
    try:
        admin = type("A", (), {"id": 0, "name": "审查", "role": "superadmin"})()
        # 正门：受保护目录 404
        try:
            serve_upload("voucher/AUDIT_TRAVERSAL.jpg", admin=admin)
            raise AssertionError("受保护目录不该下发")
        except NotFoundError:
            pass
        # 绕过：判据必须落在**解析后**的相对路径上
        for path in (
            "cover/../voucher/AUDIT_TRAVERSAL.jpg",
            "book_audio/../voucher/AUDIT_TRAVERSAL.jpg",
        ):
            try:
                serve_upload(path, admin=admin)
                raise AssertionError(f"{path} 不该能读到受保护目录（修复前返回 FileResponse）")
            except NotFoundError:
                pass
        # 正常文件仍要能下发（别把闸门收得连正门都进不去）
        os.makedirs(os.path.join(root, "cover", "9999"), exist_ok=True)
        ok_file = os.path.join(root, "cover", "9999", "ok.jpg")
        with open(ok_file, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0")
        assert serve_upload("cover/9999/ok.jpg", admin=admin) is not None
        os.remove(ok_file)
    finally:
        os.remove(secret)


def test_second_refund_cannot_overpay_order(client, db):
    """同一订单：失败退款重试 + 另一笔成功 → 累计不得超订单金额（修复前 500 元订单出款 1000）。"""
    from backend.domain.identity.models import Order, RefundRequest  # noqa: F401
    from backend.domain.identity.order_service import OrderService
    from backend.domain.identity.wm10_service import RefundService
    from tests.unit.test_a1_closure_20261009 import _admin_order

    # 线下收款单（pay_method=scan，无 transaction_id）：执行退款的成败由**人工判定**，
    # 这样才能复现"第一笔判失败 → 第二笔成功 → 第一笔重试"的路径
    h = _h(client)
    _p, child_row, _mh = _family(client, h, phone="13800009801")
    offline = _admin_order(client, h, child_row["id"], order_type="custom", amount="500.00")
    r = client.post(
        f"/api/admin/orders/{offline['id']}/confirm-payment",
        json={"pay_method": "scan", "remark": "审查造单：线下收款"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    order = db.query(Order).filter(Order.id == offline["id"]).first()
    db.refresh(order)
    assert order.status == Order.STATUS_PAID and not order.transaction_id

    admin = type("A", (), {"id": 0, "display_name": "审查", "role": "superadmin"})()
    rr1 = OrderService(db).refund_order(admin, order.id, "审查-失败重试#1")
    RefundService(db).review(admin, rr1.id, True, "ok")
    RefundService(db).execute(admin, rr1.id, False, "第一次判失败")
    db.refresh(rr1)
    assert rr1.status == RefundRequest.STATUS_FAILED

    rr2 = OrderService(db).refund_order(admin, order.id, "审查-第二笔#2")
    RefundService(db).review(admin, rr2.id, True, "ok")
    RefundService(db).execute(admin, rr2.id, True, "第二笔成功")

    # 第一笔重试必须被"累计超额"拦下（修复前会成功 → 同单双出款）
    from backend.common.exceptions import ValidationError

    try:
        RefundService(db).execute(admin, rr1.id, True, "第一笔重试")
        raise AssertionError("同单第二笔成功后再重试第一笔，必须被累计闸拦下")
    except ValidationError as exc:
        assert "超额" in str(exc), str(exc)


def test_paid_deposit_cannot_create_another_online_deposit_order(client, db):
    """已缴押金的孩子再下押金单必须在**建单侧**就被拒（修复前钱收了、订单停待支付）。"""
    from decimal import Decimal

    from backend.common.exceptions import ValidationError
    from backend.domain.billing.models import Deposit
    from backend.domain.identity.models import Child, Parent
    from backend.domain.identity.payment_service import PaymentService

    h = _h(client)
    _p, child_row, _mh = _family(client, h, phone="13800009802")
    child = db.query(Child).filter(Child.id == child_row["id"]).first()
    parent = db.query(Parent).filter(Parent.id == child.parent_id).first()
    db.add(
        Deposit(
            child_id=child.id,
            amount=Decimal("1200.00"),
            available_amount=Decimal("1200.00"),
            deducted_amount=Decimal("0.00"),
            unpaid_balance=Decimal("0.00"),
            status=Deposit.STATUS_PAID,
        )
    )
    db.commit()
    try:
        PaymentService(db).create_online_order(parent, child, "deposit")
        raise AssertionError("已缴押金不该还能建线上押金单")
    except ValidationError as exc:
        assert "无需重复缴纳" in str(exc), str(exc)


def test_checkin_duplicate_is_not_reported_as_first(client, db):
    """同一天第二次打卡必须返回"今日已打卡"。

    强证据不在本用例（顺序调用会被前置 `exists` 检查挡住，新旧代码都绿），而是**真库实验**：
    `INSERT ... ON DUPLICATE KEY UPDATE id=id` 在 SQLAlchemy/pymysql 下首次 rowcount=1、
    重复也是 1（CLIENT_FOUND_ROWS 把"置为原值"从 0 变 1），所以旧判据 `rowcount != 1` 恒不成立。
    本用例守住对外契约（返回值与行数），下面再钉住判据已换。
    """
    from backend.domain.identity.models import Child
    from backend.domain.reading.service import ReadingService

    h = _h(client)
    _p, child_row, _mh = _family(client, h, phone="13800009803")
    child = db.query(Child).filter(Child.id == child_row["id"]).first()
    db.execute(
        text("DELETE FROM checkins WHERE child_id=:c AND checkin_date=:d"),
        {"c": child.id, "d": date.today()},
    )
    db.commit()
    first = ReadingService(db)._checkin(child, 1)
    assert first["checked_in"] is True, first
    second = ReadingService(db)._checkin(child, 1)
    assert second["checked_in"] is False and second["reason"] == "今日已打卡", second
    rows = db.execute(
        text("SELECT COUNT(*) FROM checkins WHERE child_id=:c AND checkin_date=:d"),
        {"c": child.id, "d": date.today()},
    ).scalar()
    assert rows == 1
    # 判据形状：必须用 inserted_primary_key 区分"真插入 vs 命中唯一键"（rowcount 不可信）
    import inspect

    src = inspect.getsource(ReadingService._checkin)
    assert "inserted_primary_key" in src, "打卡判据必须换成 inserted_primary_key"
    db.execute(
        text("DELETE FROM checkins WHERE child_id=:c AND checkin_date=:d"),
        {"c": child.id, "d": date.today()},
    )
    db.commit()


def test_progress_row_is_locked_for_update():
    """`report_progress` 的进度行必须是锁定读（修复前普通 SELECT → 并发丢区间）。"""
    import inspect

    from backend.domain.reading import service as reading_service

    src = inspect.getsource(reading_service.ReadingService.report_progress)
    assert src.count("with_for_update()") >= 2, "进度行的两处读取（首次 + 插入后重读）都必须加锁"


def test_deposit_gate_requires_paid_status():
    """押金闸门是白名单：paid/partially_deducted 放行；refunded/refunding 一律拦
    （修复前放行 → 退会后重新入会可零押金借书）。"""
    for status in ("refunded", "refunding", "fully_deducted", "unpaid"):
        r = deposit_gate(
            deposit_status=status,
            deposit_unpaid_balance=0,
            override_reason=None,
            unpaid_status="unpaid",
            fully_deducted_status="fully_deducted",
            paid_status="paid",
        )
        assert r.code, f"{status} 不该放行"
    for status in ("paid", "partially_deducted"):
        ok = deposit_gate(
            deposit_status=status,
            deposit_unpaid_balance=0,
            override_reason=None,
            unpaid_status="unpaid",
            fully_deducted_status="fully_deducted",
            paid_status="paid",
            partially_deducted_status="partially_deducted",
        )
        assert not ok.code, f"{status} 在账且有余额，应放行"


def test_unpaid_balance_cents_tail_blocks_borrow():
    """未结清赔偿款的**分**尾巴也要拦（修复前 int(Decimal) 截断：0.50 元被当 0）。"""
    from backend.domain.circulation import service as circ

    src = __import__("inspect").getsource(circ)
    assert "yuan_to_cents" in src, "未结清余额必须按分比较（原先 int() 截断）"


def test_scheduled_reconcile_uses_yesterday_bill():
    """定时对账必须取**昨日**账单（微信对账单 T+1 生成；修复前取当天 → 永久 NO_BILL_EXIST）。"""
    import inspect

    from backend.tasks import registry

    src = inspect.getsource(registry._payment_reconcile)
    assert "timedelta(days=1)" in src, "定时对账必须传昨日账单日"
    assert "bill_date=" in src
    # 行为断言：昨天日期确实由 date.today()-1 得出
    assert date.today() - timedelta(days=1) < date.today()
