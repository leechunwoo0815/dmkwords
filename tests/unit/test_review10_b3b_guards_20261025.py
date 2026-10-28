# tests/unit/test_review10_b3b_guards_20261025.py — 第十轮整改 B3（下）：13 条 P3 的判据
"""B3 批（下）覆盖：`FE-3` `W1-2` `W1-3` `W1-5` `W1-6` `W1-7` `W1-8` `W2-D2` `W2-D3` `W2-D4`
`W2-D5` `W2-D6` `W2-D8`（余 `W1-4` 状态机矩阵 / `W2-D7` 配置副本守卫见后续批）。

每条都写清 **RED（修复前的形状）**：拿掉修复即红。
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.common.exceptions import ConflictError
from backend.database import SessionLocal
from backend.domain.billing.models import Deposit
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.identity.models import Child, Order, Parent
from backend.domain.reading.models import Reservation
from backend.domain.reading.reservation_service import ReservationService

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: 每次建孩子换一个手机号/ISBN（两个唯一索引；与 B3(上) 文件同款做法）
_SEQ = iter(range(4101, 4999))


def _parent_child(db, *, status: str = Child.MEMBER_FORMAL) -> Child:
    parent = Parent(name="B3b 家长", phone=f"1390001{next(_SEQ)}")
    db.add(parent)
    db.flush()
    child = Child(
        parent_id=parent.id,
        name="B3b 孩子",
        member_status=status,
        member_expire=date.today() + timedelta(days=30),
    )
    db.add(child)
    db.flush()
    return child


def _book_with_copy(db, copies: int = 1) -> Book:
    book = Book(
        isbn=f"9780001{next(_SEQ):06d}",
        title="B3b 靶书",
        author="probe",
        word_count=100,
        status=Book.STATUS_ON,
    )
    db.add(book)
    db.flush()
    for i in range(copies):
        db.add(
            BookCopy(
                book_id=book.id,
                copy_code=f"B3B-{book.id}-{i}",
                status=BookCopy.STATUS_AVAILABLE,
            )
        )
    db.commit()
    return book


def _paid_deposit(db, child: Child) -> None:
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


# ---------------------------------------------------------------- FE-3 预约懒过期


def test_fe3_create_releases_overdue_reservation_first(db):
    """FE-3：建单前先**就地释放**到期单（调度器停摆时的兜底）。

    RED：`create()` 只看 `status == active` —— 一张"已过期但调度器没跑"的预约会继续
    占着副本（reserved）与借阅额度，于是同一孩子预约同书被"该书已有进行中的预约"顶回、
    别的孩子也约不到那本（副本被锁死）。修复后：先 `expire_due()`（与调度器同一函数）再计数。
    """
    child = _parent_child(db)
    _paid_deposit(db, child)
    book = _book_with_copy(db)

    # 一张"已过期、但没人释放"的预约（调度器没跑到的现场）
    stale = Reservation(
        child_id=child.id,
        book_id=book.id,
        copy_id=db.query(BookCopy).filter(BookCopy.book_id == book.id).first().id,
        status=Reservation.STATUS_ACTIVE,
        expires_at=datetime.now() - timedelta(hours=2),
    )
    db.add(stale)
    db.flush()
    stale_copy = db.query(BookCopy).filter(BookCopy.book_id == book.id).first()
    stale_copy.status = BookCopy.STATUS_RESERVED
    db.commit()

    fresh = ReservationService(db).create(child, book.id)

    db.expire_all()
    assert fresh.id != stale.id
    assert db.get(Reservation, stale.id).status == Reservation.STATUS_EXPIRED, (
        "到期单必须先被释放（否则它继续占额度/占副本）"
    )
    assert db.get(Reservation, fresh.id).status == Reservation.STATUS_ACTIVE


def test_fe3_list_mine_hides_overdue_unreleased(db):
    """FE-3 读路径：`list_mine` 不许把"已过期还没释放"的单子当"预约中"（读路径不装活）。

    RED：原先只按 `status == active` 过滤 → 家长看到"预约中"，到店却取不到书。
    """
    child = _parent_child(db)
    _paid_deposit(db, child)
    book = _book_with_copy(db)
    db.add(
        Reservation(
            child_id=child.id,
            book_id=book.id,
            copy_id=db.query(BookCopy).filter(BookCopy.book_id == book.id).first().id,
            status=Reservation.STATUS_ACTIVE,
            expires_at=datetime.now() - timedelta(minutes=5),
        )
    )
    db.commit()

    active_view = ReservationService(db).list_mine(child, include_closed=False)
    assert active_view == [], f"过期未释放的单子不该出现在「预约中」：{active_view}"
    history = ReservationService(db).list_mine(child, include_closed=True)
    assert len(history) == 1, "「我的预约」历史仍要能看到它（口径不变）"


# ---------------------------------------------------------------- W1-2 结算矩阵


def _pending_order(db, child: Child) -> Order:
    order = Order(
        order_no=f"DMK-T{next(_SEQ)}",
        order_type=Order.TYPE_DEPOSIT,
        parent_id=child.parent_id,
        child_id=child.id,
        amount=Decimal("1200.00"),
        status=Order.STATUS_PENDING_MANUAL,
    )
    db.add(order)
    db.commit()
    return order


def test_w1_2_settle_refuses_cancelled_order(db):
    """W1-2：`cancelled` 订单不得被结算成 `paid`（矩阵里这条边不存在）。

    RED：`_settle_paid` 无条件 `order.status = paid` → 取消与支付并发时（家长点了取消 /
    超时单被清，同时回调或 mock 即时通道到达）钱入账、会员/押金/报名联动全跑，
    而 `Order.ALLOWED_TRANSITIONS[CANCELLED] = set()`。
    """
    from backend.domain.identity.order_service import OrderService

    child = _parent_child(db)
    order = _pending_order(db, child)
    order.status = Order.STATUS_CANCELLED
    db.commit()

    with pytest.raises(ConflictError) as ei:
        OrderService(db)._settle_paid(
            order,
            actor=None,
            pay_method="wechat",
            remark="",
            payer_id=None,
            action="order.payment_callback",
            reason="并发取消窗口",
        )
    assert "不能结算" in str(ei.value), str(ei.value)
    db.expire_all()
    assert db.get(Order, order.id).status == Order.STATUS_CANCELLED, "拒绝后订单状态不得被改写"


def test_w1_2_settle_refuses_already_paid(db):
    """W1-2 对照：已 `paid` 的订单再结算 → 明确拒绝（不再把联动跑第二遍）。"""
    from backend.domain.identity.order_service import OrderService

    child = _parent_child(db)
    order = _pending_order(db, child)
    order.status = Order.STATUS_PAID
    db.commit()

    with pytest.raises(ConflictError):
        OrderService(db)._settle_paid(
            order,
            actor=None,
            pay_method="wechat",
            remark="",
            payer_id=None,
            action="order.payment_callback",
            reason="重复结算",
        )


def test_w1_2_prepay_phase3_rejects_cancelled():
    """W1-2 前半段：prepay Phase 3 复核发现订单已取消 → 不再下发支付参数。

    RED：原先 Phase 3 只判 `status == PAID`（回调赢了才认），`cancelled` 直接往下走：
    真实通道把已取消订单的 pay_params 交给家长（家长照付 → 回调被拒 → 客服），
    mock 即时通道更会直接结算成 paid。
    """
    from backend.domain.identity.payment_service import PaymentService

    db = SessionLocal()
    try:
        child = _parent_child(db)
        order = _pending_order(db, child)
        order.status = Order.STATUS_CANCELLED
        db.commit()

        # 源码面判据（可构造的运行时场景需要真实网关+mock 通道：见 cron 前的 fallback 分支）
        src = (ROOT / "backend/domain/identity/payment_service.py").read_text(encoding="utf-8")
        code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
        assert "if not fresh.can_transition(Order.STATUS_PAID):" in code, (
            "Phase 3 必须用矩阵判定拦下非可结算状态"
        )
        assert hasattr(PaymentService, "prepay")
    finally:
        db.rollback()
        db.close()


# ---------------------------------------------------------------- W1-3 押金单审计


def test_w1_3_deposit_order_writes_audit(db):
    """W1-3：管理端建押金单必须留审计（与同域补缴单同款）。

    RED：`create_deposit_order` 只 add/flush/commit，`admin` 参数甚至未被使用——
    资金动作没有 actor/时间/原因可回溯。
    """
    from backend.common.system_models import AuditLog
    from backend.domain.admin.models import AdminUser
    from backend.domain.billing.service import DepositService

    admin = db.query(AdminUser).filter(AdminUser.username == "admin").first()
    assert admin is not None, "演示库应有 admin 账号（conftest 已 seed）"
    child = _parent_child(db)
    order = DepositService(db).create_deposit_order(admin, child.id)

    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "deposit.create_order", AuditLog.target_id == str(order.child_id)
        )
        .all()
    )
    assert rows, "建押金单必须写审计"
    assert rows[-1].actor_id == admin.id, "审计要记操作人"


def test_w1_3_audit_action_has_frontend_label():
    """铁律 12：新增审计动作必须同步管理端 `AuditLog.tsx` 的标签，否则看板显示原始 action。"""
    text = (ROOT / "admin-web/src/pages/AuditLog.tsx").read_text(encoding="utf-8")
    assert '"deposit.create_order"' in text
    assert "创建押金单" in text


# ---------------------------------------------------------------- W1-5 文案读配置


def test_w1_5_quiz_attempts_message_uses_config():
    """W1-5：测验次数耗尽的报错文案必须读配置（原先写死「3 次」）。

    RED：`quiz_max_attempts` 改成 5 时，数据面说"还剩 5 次"、报错说"3 次"（同一接口两种口径）。
    """
    src = (ROOT / "backend/domain/growth/service.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    assert "小挑战机会已用完（3 次）" not in code, "文案不许写死 3"
    assert 'f"小挑战机会已用完（{max_attempts} 次）"' in code, "文案必须引用 max_attempts"


# ---------------------------------------------------------------- W1-6 装饰器


def test_w1_6_payment_notify_has_no_nested_staticmethod():
    """W1-6：`_decrypt` 的双层 `@staticmethod`（`staticmethod(staticmethod(f))`）必须只剩一层。

    行为等价（Py3.10+）但工具面/可读性受损；用 AST 判据而不是文本（装饰器写法有多形态）。
    """
    tree = ast.parse(
        (ROOT / "backend/domain/identity/payment_notify.py").read_text(encoding="utf-8")
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_decrypt":
            names = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
            assert names.count("staticmethod") <= 1, f"_decrypt 装饰器重复：{names}"
            return
    raise AssertionError("未找到 _decrypt")


# ---------------------------------------------------------------- W1-7 死枚举


def test_w1_7_common_types_removed():
    """W1-7：`backend/common/types.py`（9 个零消费枚举 + 与域常量矛盾的取值）已删除，且无人引用。

    RED：留着它 = 埋雷——docstring 自称"各 model 引用"，后来者 import 它做判断即与真实常量打架
    （`MemberStatus.OFFICIAL=2` vs `Child.MEMBER_FORMAL="formal"`）。
    """
    assert not (ROOT / "backend/common/types.py").exists()
    hits = []
    me = pathlib.Path(__file__).resolve()
    for path in list(ROOT.joinpath("backend").rglob("*.py")) + list(
        ROOT.joinpath("tests").rglob("*.py")
    ):
        if path.resolve() == me:
            continue  # 本文件自己写着这个字符串（判据不算引用）
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "common.types" in text or "from backend.common import types" in text:
            hits.append(path.as_posix())
    assert hits == [], f"仍有引用：{hits}"


# ---------------------------------------------------------------- W1-8 缩略图删除守卫


def test_w1_8_thumb_cleanup_uses_is_within():
    """W1-8：旧缩略图删除守卫收口到 `media_paths.is_within`（全仓最后一处裸前缀）。

    注（诚实交代）：该处原本就带了尾分隔符（`+ os.sep`），**兄弟目录前缀骗不过它**，
    实害不成立；本条修的是"口径收口"（与全仓其余 9 处一致，防未来改动改写判据）。
    """
    src = (ROOT / "backend/domain/reading_circle/card_render.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
    assert "is_within(circle_dir, old_full)" in code
    assert "old_full.startswith(circle_dir)" not in code

    from backend.common.media_paths import is_within

    root = "/tmp/dmk-uploads"
    assert is_within(root + "/circle", "/tmp/dmk-uploads/circle/thumb_1_a.jpg")
    assert not is_within(root + "/circle", "/tmp/dmk-uploads/circle-old/thumb_1_a.jpg"), (
        "同前缀兄弟目录必须判外部"
    )


# ---------------------------------------------------------------- W2-D2 事件契约文档


WIRED_SUBSCRIBERS = {
    "OrderPaidEvent": "_on_order_paid",
    "LevelAdvancedEvent": "_on_level_up",
    "QuizPassedEvent": "_on_quiz_passed",
    "QuizFailedEvent": "_on_quiz_failed",
    "BookBorrowedEvent": "_on_book_borrowed",
    "BookReturnedEvent": "_on_book_returned",
    "DepositPaidEvent": "_on_deposit_paid",
    "ReservationCreatedEvent": "_on_reservation_created",
    "ReservationExpiredEvent": "_on_reservation_released",
    "MilestoneAchievedEvent": "_on_milestone_achieved",
}

#: N-8（2026-10-28 甲方裁定）：四个未接线事件类已删除。留名册用于**防回归**——
#: 谁把类加回来（无论有没有接线）立刻判红；重新接线属新功能，须同步补发布/订阅并审视白名单。
REMOVED_DEAD_EVENTS = (
    "ReservationFulfilledEvent",
    "ReservationCancelledEvent",
    "ReadingBookFinishedEvent",
    "ReadingSessionCompletedEvent",
)


def test_w2_d2_event_docstrings_match_reality():
    """W2-D2：事件类 docstring 的发布者/订阅者必须与实现一致，且不引用不存在的符号。

    RED：`QuizPassedEvent` 原写 5 个订阅者（advancement/child/bookshelf/borrow/achievement）
    与 `ChildLevel.quizzes_passed_at_level`——全仓都不存在；`BookBorrowedEvent` 写
    `BorrowService.borrow_book()` 而真实写口是 `CirculationService`。照着找接线点会白找一遍。
    """
    from backend.common import events as ev_mod
    from backend.common.events import event_bus

    src = (ROOT / "backend/common/events.py").read_text(encoding="utf-8")
    # 订阅面用**运行时**真表（`event_bus._handlers`），不读源码文本——文本断言容易假绿
    from backend.tasks.notify_handlers import register_notification_handlers

    register_notification_handlers()

    # ① 每个"已接线"事件：docstring 必须点名真实订阅者
    for cls, handler in WIRED_SUBSCRIBERS.items():
        tree = ast.parse(src)
        doc = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                doc = ast.get_docstring(node)
        assert doc is not None, f"{cls} 缺 docstring"
        assert handler in doc, f"{cls} 的 docstring 必须点名真实订阅者 {handler}"
        event_type = getattr(ev_mod, cls).event_type
        names = [getattr(h, "__name__", "") for h in event_bus._handlers.get(event_type, [])]
        assert handler in names, f"{cls}（{event_type}）的真实订阅者是 {names}，不是 {handler}"

    # ② 四个未接线事件类已按 N-8 裁定删除——防回归守卫见下方独立用例

    # ③ **声明行**（发布者 / `- 订阅者` 行）不得引用不存在的 `X.y` 符号。
    #    只看声明行、不看说明文字：订正注释里会**引用**旧错误写法（"原文写的 X 全仓不存在"），
    #    那是留给后来者的证据，不是声明。
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ClassDef) and node.name.endswith("Event"):
            doc = ast.get_docstring(node) or ""
            declared = [
                ln.strip()
                for ln in doc.split("\n")
                if ln.strip().startswith("- ") or ln.strip().startswith("发布者：")
            ]
            for token in (
                "ChildLevel.quizzes_passed_at_level",
                "BorrowService.",
                "BookshelfService",
                "AdvancementService.",
            ):
                assert not any(token in ln for ln in declared), (
                    f"{node.name} 的声明行仍引用不存在的符号 {token}"
                )
    assert ev_mod.__name__ == "backend.common.events"  # 模块可加载（事件面契约不被改坏）


def test_n8_dead_events_removed_and_whitelist_empty():
    """N-8（2026-10-28 甲方裁定）：四个零发布零订阅的事件类已删除，白名单清零。

    RED：删类不删白名单 → `verify_architecture` 判「白名单条目已不再命中任何问题」；
    把任一死类加回来（仍不接线）→ 本用例直接红，防止「先定契约后接线」的空壳回潮。
    """
    from backend.common import events as ev_mod
    from scripts.verify_architecture import EVENT_CONTRACT_WHITELIST

    for cls in REMOVED_DEAD_EVENTS:
        assert not hasattr(ev_mod, cls), (
            f"{cls} 已按 N-8 裁定删除，不得重新引入（重新接线须同步补发布/订阅）"
        )
    assert EVENT_CONTRACT_WHITELIST == {}, (
        "死事件类已删除，事件契约白名单必须为空（过期条目会被门禁判红）"
    )


# ---------------------------------------------------------------- W2-D3 对账幂等


def test_w2_d3_reconcile_record_is_idempotent(db):
    """W2-D3：同日同来源重跑 → **原地更新**而不是再插一行（审计者要能分辨"跑了几轮"）。

    RED：`_record` 无条件 add → 同一天手动重跑一次就多一条（列表/导出 CSV 都跟着重复）。
    """
    from backend.domain.identity.payment_models import PaymentReconciliation
    from backend.domain.identity.payment_reconcile_service import PaymentReconcileService

    svc = PaymentReconcileService(db)
    day = date(2026, 10, 25)
    first = svc._record(
        source=PaymentReconciliation.SOURCE_LOCAL,
        day=day,
        checked=3,
        diffs=[],
        trigger=PaymentReconciliation.TRIGGER_MANUAL,
    )
    second = svc._record(
        source=PaymentReconciliation.SOURCE_LOCAL,
        day=day,
        checked=5,
        diffs=[],
        trigger=PaymentReconciliation.TRIGGER_MANUAL,
    )
    rows = (
        db.query(PaymentReconciliation)
        .filter(
            PaymentReconciliation.bill_date == day,
            PaymentReconciliation.source == PaymentReconciliation.SOURCE_LOCAL,
            PaymentReconciliation.is_deleted == 0,
        )
        .all()
    )
    assert len(rows) == 1, f"同日同来源只该有一行，实际 {len(rows)} 行"
    assert first["id"] == second["id"], "第二轮应是原地更新（同一行 id）"
    detail = json.loads(rows[0].detail)
    assert detail["run_count"] == 2, f"run_count 要记到 2：{detail}"
    assert rows[0].checked_count == 5, "内容取最近一轮"


# ---------------------------------------------------------------- W2-D4 任务锁与重试


def test_w2_d4_task_lock_blocks_concurrent_run(db):
    """W2-D4：同一任务并发只能跑一份；手动触发抢不到锁时**明确报错**。

    RED：手动触发有意不受调度器领导锁限制（人按按钮要响应），于是"手动 + 自动"会撞车——
    TaskRunLog 双条、幂等不彻底的任务重复处理。
    """
    import threading
    import time

    from backend.tasks import registry as reg

    release = threading.Event()

    def _slow(_db):
        release.wait(timeout=10)
        return 1

    reg.TASKS["_b3b_probe"] = reg.TaskSpec(
        name="_b3b_probe",
        display_name="B3b 探针任务",
        group="测试",
        interval_seconds=9999,
        fn=_slow,
    )
    try:
        started = threading.Event()

        def _first():
            started.set()
            reg.run_task("_b3b_probe")

        t = threading.Thread(target=_first, daemon=True)
        t.start()
        started.wait(timeout=5)
        time.sleep(0.4)  # 让第一份进入 fn（锁已持有）

        with pytest.raises(ConflictError) as ei:
            reg.run_task("_b3b_probe", manual=True)
        assert "正在运行中" in str(ei.value), str(ei.value)

        release.set()
        t.join(timeout=10)
    finally:
        reg.TASKS.pop("_b3b_probe", None)


def test_w2_d4_failed_schedule_retry_once():
    """W2-D4：失败任务被排一次**一次性**重试（`is_retry=True`），且不无限自我复制。

    RED：原先失败就此结束——cron 任务要等到明天，期间无任何兜底。
    """
    from backend.tasks import registry as reg
    from backend.tasks.task_guard import schedule_retry

    jobs: list[dict] = []

    class _FakeScheduler:
        def add_job(self, fn, **kw):
            jobs.append({"fn": fn, **kw})

    assert schedule_retry(_FakeScheduler(), "probe", reg.guarded_run, misfire_grace=300)
    assert len(jobs) == 1
    assert jobs[0]["id"] == "probe:retry"
    assert jobs[0]["kwargs"] == {"is_retry": True}, "重试那次必须带 is_retry（否则无限重试）"
    assert schedule_retry(None, "probe", reg.guarded_run, misfire_grace=300) is False


# ---------------------------------------------------------------- W2-D5 押金端点 404


def test_w2_d5_deposit_endpoint_404_for_missing_child(client, admin_headers):
    """W2-D5：不存在的孩子 404（口径与 `/children/{id}` 等同族端点一致）。

    RED：原先 `200 + null` —— "ID 打错"与"这孩子没缴过押金"不可区分，容易照着错号收第二笔。
    """
    resp = client.get("/api/admin/deposits/children/99999999", headers=admin_headers)
    assert resp.status_code == 404, resp.text


def test_w2_d5_deposit_endpoint_null_when_no_deposit(client, admin_headers, db):
    """W2-D5 对照：孩子存在但还没有押金账户 → 仍 `200 + null`（口径不变）。"""
    child = _parent_child(db)
    db.commit()
    resp = client.get(f"/api/admin/deposits/children/{child.id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() is None


# ---------------------------------------------------------------- W2-D6 uploads 双通道


def test_w2_d6_uploads_accepts_query_token(client, admin_headers):
    """W2-D6：`/api/admin/uploads/{path}` 支持 `?token=`（与同族媒体端点一致）。

    为什么：`<img src>` / `innerAudioContext` 带不了 `Authorization` 头。判据用 404 而不是
    200——**404 恰证明凭证过了鉴权**（路径不存在才 404）；401/403 说明凭证通道没通。
    """
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    resp = client.get(f"/api/admin/uploads/nope-not-there.jpg?token={token}")
    assert resp.status_code == 404, resp.text
    resp2 = client.get("/api/admin/uploads/nope-not-there.jpg")
    assert resp2.status_code in (401, 403), f"无凭证必须拒绝：{resp2.status_code}"


# ---------------------------------------------------------------- W2-D8 排行榜缺参语义


def test_w2_d8_leaderboard_missing_child_id_is_param_error(client, db):
    """W2-D8：缺 `child_id` 时给**参数语义**的报错（原先 422「孩子不存在」误导）。

    RED：默认值 0 直接进归属校验 → "孩子不存在"，家长/客服据此以为孩子数据没了。
    """
    child = _parent_child(db)
    db.commit()
    phone = db.get(Parent, child.parent_id).phone
    login = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).json()
    headers = {"Authorization": f"Bearer {login['token']}"}
    resp = client.get("/api/miniapp/leaderboard?period=total", headers=headers)
    assert resp.status_code in (400, 422), resp.text
    assert "child_id" in resp.text, f"报错要说清缺哪个参数：{resp.text}"
