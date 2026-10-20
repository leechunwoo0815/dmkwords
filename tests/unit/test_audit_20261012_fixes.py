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


def test_progress_row_is_locked_for_update(client, session_pair):
    """`report_progress` 的进度行必须是**锁定读**——无锁时"读快照 → 合并区间 → 整列覆盖写"
    在并发心跳（双开播放器 / 心跳重试）下会丢区间（后到者用陈旧快照覆盖先到者）。

    **行为判据**（替代原先的 `inspect.getsource(...).count("with_for_update()") >= 2` 源码字符串断言
    ——那种断言改实现就失效：把锁定读换成普通读、甚至换成别处的两处 with_for_update，它照样绿）：
    先到者持该进度行锁 → 后来者的心跳上报必须**阻塞在 `SELECT ... FOR UPDATE`** 上。
    RED（把锁定读换成普通读）：后来者的读直接通过、只有随后的 UPDATE 等锁 → 前缀断言红。
    """
    import threading

    from backend.database import get_session
    from backend.domain.reading.models import ReadingProgress
    from tests.unit.helpers import assert_locked_read, lock_wait_query
    from tests.unit.test_wm6_reading import _backdate, _report, _setup

    h = _h(client)
    c, book, mini = _setup(client, h, "13981200001")
    assert _report(client, mini, c["id"], book["id"], 10, 0).status_code == 200

    with get_session() as db:
        pid = (
            db.query(ReadingProgress.id)
            .filter(
                ReadingProgress.child_id == c["id"],
                ReadingProgress.book_id == book["id"],
                ReadingProgress.is_deleted == 0,
            )
            .scalar()
        )
    assert pid, "前置失败：进度行没建出来"

    # 防刷协议按**服务端墙上时钟**判覆盖增速：上报前把 last_report_at 拨回（模拟真实流逝）。
    # 必须在**取锁之前**做——`_backdate` 自身是 UPDATE，锁后再调会等锁超时。
    _backdate(client, c["id"], book["id"], 60)

    _s1, s2 = session_pair
    locked = (
        s2.query(ReadingProgress)
        .filter(ReadingProgress.id == pid)
        .with_for_update()
        .populate_existing()
        .first()
    )
    assert locked is not None

    result: dict = {}

    def _heartbeat():
        result["r"] = _report(client, mini, c["id"], book["id"], 40, 30)

    t = threading.Thread(target=_heartbeat)
    t.start()
    try:
        assert_locked_read(lock_wait_query(), "并发心跳等的进度行锁定读")
    finally:
        s2.commit()  # 释放锁
    t.join(timeout=15)
    assert not t.is_alive(), "心跳线程 15s 未结束（疑似死锁）"
    assert result["r"].status_code == 200, result["r"].text


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


def test_unpaid_balance_cents_tail_blocks_borrow(client):
    """未结清赔偿款的**分**尾巴也要拦（修复前 `int(Decimal)` 截断：0.50 元被当成 0 → 放行）。

    **行为判据**（替代原先的 `"yuan_to_cents" in getsource(circ)` 源码字符串断言——那种断言
    只要模块里出现过这个词就绿，改回 `int(...)` 照样通过）：真造一笔 **0.50 元**未结清赔偿款，
    走**真实借书接口**，必须被拦且文案带「0.50 元」（截断成 0 时会静默放行）。
    """
    from decimal import Decimal

    from backend.database import get_session
    from backend.domain.billing.models import Deposit
    from backend.domain.catalog.models import BookCopy
    from tests.unit.test_wm10_concurrency import _book_with_copies, _pay, _pay_deposit

    h = _h(client)
    _p, c, _mini = _family(client, h, "13981200002")
    _pay(client, h, c["id"], "observation_fee")  # 先入会（否则闸门先命中「未入会」分支）
    _pay_deposit(client, h, c["id"])
    book = _book_with_copies(client, h, "分尾巴书", 1)
    with get_session() as db:
        copy_id = db.query(BookCopy).filter(BookCopy.book_id == book).first().id
        dep = db.query(Deposit).filter(Deposit.child_id == c["id"]).first()
        dep.unpaid_balance = Decimal("0.50")  # 分尾巴（截断成 int 就是 0）
        db.commit()

    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copy_id},
        headers=h,
    )
    assert r.status_code != 200, f"有未结清赔偿款不得借书，实 {r.status_code} {r.text[:160]}"
    detail = r.json().get("detail", "")
    assert "未结清赔偿款 0.50 元" in detail, (
        f"拦截文案必须按分显示 0.50 元（int 截断会显示 0 元甚至放行），实 {detail!r}"
    )


def test_scheduled_reconcile_uses_yesterday_bill():
    """定时对账必须取**昨日**账单（微信对账单 T+1 生成；修复前取当天 → 永久 NO_BILL_EXIST）。

    **行为判据**（替代原先的 `"timedelta(days=1)" in getsource(...)` 源码字符串断言 +
    `date.today()-1 < date.today()` 恒真式——后者与实现无关，恒成立）：把对账服务的 `run`
    换成捕获器，断言任务**实际传下去的 `bill_date` 就是昨天**、`trigger` 是 scheduled。
    RED（改回不传 / 传今天）：捕获到的 `bill_date` 不是昨天。
    """
    from backend.database import get_session
    from backend.domain.identity.payment_models import PaymentReconciliation
    from backend.domain.identity.payment_reconcile_service import PaymentReconcileService
    from backend.tasks import registry

    captured: dict = {}
    original = PaymentReconcileService.run

    def _capture(self, bill_date=None, trigger=None):
        captured["bill_date"] = bill_date
        captured["trigger"] = trigger
        return {"diff_total": 0}

    PaymentReconcileService.run = _capture
    try:
        with get_session() as db:
            diff = registry._payment_reconcile(db)
    finally:
        PaymentReconcileService.run = original

    assert diff == 0
    assert captured.get("bill_date") == date.today() - timedelta(days=1), (
        f"定时对账必须传**昨日**账单日，实传 {captured.get('bill_date')}"
    )
    assert captured.get("trigger") == PaymentReconciliation.TRIGGER_SCHEDULED
