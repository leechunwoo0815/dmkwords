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
    SCENE_RESERVATION_EXPIRING,
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
        title="测验成绩",
        content="孩子的测验已通过，成绩已计入阅读档案。",
        category=Notification.CATEGORY_READING,
        child_id=event.child_id,
        ref_type="child",
        ref_id=str(event.child_id),
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
        title="测验成绩",
        content="孩子本次测验未通过，还有机会再次挑战。",
        category=Notification.CATEGORY_READING,
        child_id=event.child_id,
        ref_type="child",
        ref_id=str(event.child_id),
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


def _on_deposit_paid(event: DepositPaidEvent, db: Session) -> None:
    from backend.domain.identity.models import Child

    child = db.get(Child, event.child_id)
    if not child:
        return
    NotificationService(db).send_event(
        event,
        parent_id=child.parent_id,
        scene=SCENE_MONEY_DEPOSIT_PAID,
        title="押金补缴成功",
        content=f"押金补缴成功，金额 {event.amount}。",
        category=Notification.CATEGORY_MONEY,
        child_id=child.id,
        ref_type="deposit",
        ref_id=str(event.deposit_id),
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
        scene=SCENE_RESERVATION_EXPIRING,
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
    from backend.domain.identity.models import Child

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
        openid=_parent_openid(db, child.parent_id),
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
