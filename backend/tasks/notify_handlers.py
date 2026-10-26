# backend/tasks/notify_handlers.py — 事件 → 通知订阅器（WM11）
"""横切编排：订阅业务事件生成站内消息 + 微信尽力通知。

- 放 tasks（公共编排层）而非 common：handler 需查询业务域（Child/Parent/Book），
  common 禁止依赖业务域（架构关）；
- NotificationService（common）只做纯写，不依赖任何域。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.common.events import (
    BookBorrowedEvent,
    BookReturnedEvent,
    DepositPaidEvent,
    LevelAdvancedEvent,
    MilestoneAchievedEvent,
    OrderPaidEvent,
    QuizFailedEvent,
    QuizPassedEvent,
    ReservationCreatedEvent,
    ReservationExpiredEvent,
)
from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_BORROW_RETURNED,
    SCENE_BORROW_SUCCESS,
    SCENE_MONEY_DEPOSIT_PAID,
    SCENE_MONEY_ORDER_PAID,
    SCENE_READING_LEVEL_UP,
    SCENE_READING_MILESTONE,
    SCENE_READING_QUIZ_RESULT,
    SCENE_RESERVATION_CREATED,
    SCENE_RESERVATION_RELEASED,
    NotificationService,
)


def _parent_openid(db: Session, parent_id: int) -> str | None:
    """取家长 openid（微信订阅尽力通道的接收人）。"""
    from backend.domain.identity.models import Parent

    row = db.execute(
        select(Parent.wechat_openid).where(Parent.id == parent_id, Parent.is_deleted == 0)
    ).scalar_one_or_none()
    return row


def _on_order_paid(event: OrderPaidEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_MONEY_ORDER_PAID,
        title="付款成功",
        content=f"订单（{event.order_type}）已支付成功，金额 {event.amount}。",
        category=Notification.CATEGORY_MONEY,
        child_id=child.id,
        ref_type="order",
        ref_id=str(event.order_id),
        openid=_parent_openid(db, child.parent_id),
    )


def _quiz_dedup_key(event: object, outcome: str) -> str:
    """测验成绩通知的去重键（P1 #2，2026-10-15 大审查）。

    PRD §十 明文「测验成绩（**每次提交后**）」→ 一条提交一条通知。
    键 = 提交记录 id（`QuizAttempt.id`，唯一且随"重置测试机会"也不会复用）；
    极端情况下事件没带提交号（外部发布方/历史代码）时退化为发生时刻——
    **宁可多发一条，也不要把"通过"永久吞掉**（默认键 "1" 正是那个坑）。
    """
    submission_id = getattr(event, "submission_id", None)
    if submission_id:
        return f"{outcome}-sub-{submission_id}"
    occurred_at = getattr(event, "occurred_at", None)
    stamp = occurred_at.strftime("%Y%m%d%H%M%S%f") if occurred_at else "unknown"
    return f"{outcome}-at-{stamp}-b{getattr(event, 'book_id', 0)}"


def _on_level_up(event: LevelAdvancedEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    row = db.execute(
        select(Child.parent_id).where(Child.id == event.child_id, Child.is_deleted == 0)
    ).scalar_one_or_none()
    if not row:
        return
    parent_id = row
    NotificationService(db).send_event(
        event,
        parent_id=parent_id,
        scene=SCENE_READING_LEVEL_UP,
        title="等级升级",
        content=f"孩子阅读等级从 {event.from_level} 升级到 {event.to_level}！",
        category=Notification.CATEGORY_READING,
        child_id=event.child_id,
        ref_type="child",
        ref_id=str(event.child_id),
        # P1 #2（2026-10-15 大审查）：去重键=升级跃迁（from→to）。默认 "1" 会让同一孩子
        # **终身只收到第一条**升级通知（第二次升级起静默吞掉）。
        dedup_key=f"{event.from_level}->{event.to_level}",
        openid=_parent_openid(db, parent_id),
    )


def _on_quiz_passed(event: QuizPassedEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    row = db.execute(
        select(Child.parent_id).where(Child.id == event.child_id, Child.is_deleted == 0)
    ).scalar_one_or_none()
    if not row:
        return
    parent_id = row
    NotificationService(db).send_event(
        event,
        parent_id=parent_id,
        scene=SCENE_READING_QUIZ_RESULT,
        title="阅读小挑战",
        content="孩子本次阅读小挑战已通过，阅读记录已计入成长档案。",
        category=Notification.CATEGORY_READING,
        child_id=event.child_id,
        ref_type="child",
        ref_id=str(event.child_id),
        dedup_key=_quiz_dedup_key(event, "pass"),
        openid=_parent_openid(db, parent_id),
    )


def _on_quiz_failed(event: QuizFailedEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    row = db.execute(
        select(Child.parent_id).where(Child.id == event.child_id, Child.is_deleted == 0)
    ).scalar_one_or_none()
    if not row:
        return
    parent_id = row
    NotificationService(db).send_event(
        event,
        parent_id=parent_id,
        scene=SCENE_READING_QUIZ_RESULT,
        title="阅读小挑战",
        content="孩子本次阅读小挑战未通过，还有机会再次挑战。",
        category=Notification.CATEGORY_READING,
        child_id=event.child_id,
        ref_type="child",
        ref_id=str(event.child_id),
        dedup_key=_quiz_dedup_key(event, "fail"),
        openid=_parent_openid(db, parent_id),
    )


def _on_book_borrowed(event: BookBorrowedEvent, db: Session) -> None:
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    book = db.get(Book, event.book_id)
    title = book.title if book else f"书目#{event.book_id}"
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_BORROW_SUCCESS,
        title="借书成功",
        content=f"《{title}》已借出。",
        category=Notification.CATEGORY_BORROW,
        child_id=child.id,
        ref_type="borrow_record",
        ref_id=str(event.borrow_record_id),
        openid=_parent_openid(db, child.parent_id),
    )


def _on_book_returned(event: BookReturnedEvent, db: Session) -> None:
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    book = db.get(Book, event.book_id)
    title = book.title if book else f"书目#{event.book_id}"
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_BORROW_RETURNED,
        title="还书成功",
        content=f"《{title}》已归还。",
        category=Notification.CATEGORY_BORROW,
        child_id=child.id,
        ref_type="borrow_record",
        ref_id=str(event.borrow_record_id),
        openid=_parent_openid(db, child.parent_id),
    )


def _deposit_dedup_key(event: object) -> str:
    """押金到账通知的去重键（第八轮 P2-11，2026-10-21）。

    键 = **付款订单 id**：一笔订单一条通知（同一笔重复确认/回调重放被唯一索引吞掉 = 幂等），
    而**补缴是另一笔订单** → 键不同 → 不再被当成重复吞。此前不传键（默认 "1"）且
    `ref_id=deposit_id`——补缴时 `deposit_id` 不变（`deposits.child_id` 单列唯一），
    第二次缴款的五元组与首缴完全相同 → **补缴到账通知永远发不出**（钱收了、家长无凭证）。
    极端情况下事件没带订单号（外部发布方/历史代码）时退化为发生时刻——
    **宁可多发一条，也不要把补缴通知永久吞掉**（与 `_quiz_dedup_key` 同款口径）。
    """
    order_id = getattr(event, "order_id", 0)
    if order_id:
        return f"pay-order-{order_id}"
    occurred_at = getattr(event, "occurred_at", None)
    stamp = occurred_at.strftime("%Y%m%d%H%M%S%f") if occurred_at else "unknown"
    return f"pay-at-{stamp}"


def _on_deposit_paid(event: DepositPaidEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    # 首缴 vs 补缴（R-312）文案分开：此前两者都写"押金补缴成功"，首次缴纳也这么说。
    label = "押金补缴" if getattr(event, "is_supplement", False) else "押金缴纳"
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_MONEY_DEPOSIT_PAID,
        title=f"{label}成功",
        content=f"{label}成功，金额 {event.amount}。",
        category=Notification.CATEGORY_MONEY,
        child_id=child.id,
        ref_type="deposit",
        ref_id=str(event.deposit_id),
        dedup_key=_deposit_dedup_key(event),
        openid=_parent_openid(db, child.parent_id),
    )


def _on_reservation_created(event: ReservationCreatedEvent, db: Session) -> None:
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    book = db.get(Book, event.book_id)
    title = book.title if book else f"书目#{event.book_id}"
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        # 域H L-8①（T29 处置）：原复用 expiring 场景（语义错位），改专用场景
        scene=SCENE_RESERVATION_CREATED,
        title="预约成功",
        content=f"《{title}》预约成功，请及时到馆取书（72 小时内核销）。",
        category=Notification.CATEGORY_RESERVATION,
        child_id=child.id,
        ref_type="reservation",
        ref_id=str(event.reservation_id),
        openid=_parent_openid(db, child.parent_id),
    )


def _on_reservation_released(event: ReservationExpiredEvent, db: Session) -> None:
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    book = db.get(Book, event.book_id)
    title = book.title if book else f"书目#{event.book_id}"
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_RESERVATION_RELEASED,
        title="预约已释放",
        content=f"《{title}》预约已超时释放，如仍需要请重新预约。",
        category=Notification.CATEGORY_RESERVATION,
        child_id=child.id,
        ref_type="reservation",
        ref_id=str(event.reservation_id),
        openid=_parent_openid(db, child.parent_id),
    )


def _on_milestone_achieved(event: MilestoneAchievedEvent, db: Session) -> None:
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.models import Child, Parent

    child = db.get(Child, event.child_id)
    if not child:
        return
    nodes = "、".join(f"{n:,} 词" for n in event.nodes)
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_READING_MILESTONE,
        title="达成里程碑",
        content=f"孩子达成阅读里程碑（{nodes}），勋章已解锁！",
        category=Notification.CATEGORY_READING,
        child_id=child.id,
        ref_type="child",
        ref_id=str(event.child_id),
        # P1 #2（2026-10-15 大审查）：去重键=本次达成的节点集合（`_check_milestones` 只返回
        # 未发过的节点 → 每个节点终身一条）。默认 "1" 会让一个孩子**终身只收到第一个**里程碑通知。
        dedup_key="nodes-" + ",".join(str(n) for n in sorted(event.nodes)),
        openid=_parent_openid(db, child.parent_id),
    )
    # G9（2026-10-13 第五十五轮）：PRD §7.4"同时通知家长和馆员"的**馆员侧**——写管理端待办墙
    # （运营主工作面）。一个节点一条（dedup_key=节点词数，撞唯一索引静默忽略）→ 重复达成不刷屏；
    # 显示态由 todo_service 固定为"已达成·可线下表扬"，不计入待办数（知会而非待审，见 scene 常量注释）。
    parent = db.get(Parent, child.parent_id) if child.parent_id else None
    notify = AdminNotifyService(db)
    for node in event.nodes:
        notify.send(
            scene=AdminNotification.SCENE_MILESTONE_ACHIEVED,
            title=f"里程碑达成：{child.name} 累计 {node:,} 词",
            content=(
                f"{child.name} 达成阅读里程碑（累计 {node:,} 词），勋章已解锁"
                "——可安排线下表扬、朋友圈或作为颁奖盛典候选。"
            ),
            ref_type=AdminNotification.REF_CHILD,
            ref_id=child.id,
            applicant_name=f"{parent.name if parent else ''}·{child.name}",
            dedup_key=str(node),
        )


def register_notification_handlers() -> None:
    """在 main.py 启动时注册通知订阅器（模块顶层，TestClient 无 lifespan 也能跑）。"""
    from backend.common.events import event_bus

    event_bus.subscribe(OrderPaidEvent.event_type, _on_order_paid)
    event_bus.subscribe(LevelAdvancedEvent.event_type, _on_level_up)
    event_bus.subscribe(QuizPassedEvent.event_type, _on_quiz_passed)
    event_bus.subscribe(QuizFailedEvent.event_type, _on_quiz_failed)
    event_bus.subscribe(BookBorrowedEvent.event_type, _on_book_borrowed)
    event_bus.subscribe(BookReturnedEvent.event_type, _on_book_returned)
    event_bus.subscribe(DepositPaidEvent.event_type, _on_deposit_paid)
    event_bus.subscribe(ReservationCreatedEvent.event_type, _on_reservation_created)
    event_bus.subscribe(ReservationExpiredEvent.event_type, _on_reservation_released)
    event_bus.subscribe(MilestoneAchievedEvent.event_type, _on_milestone_achieved)
