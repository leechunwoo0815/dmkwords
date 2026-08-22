# backend/common/events.py
"""
[What] 同步领域事件总线
[Why] 解耦跨域操作 — QuizService 不再直接操作 Bookshelf/Child/Achievement
[How] 进程内同步事件分发，在同一事务内完成

架构意图：
  这是整个重构最重要的架构变更。

  重构前（面条代码）：
    QuizService.submit_answers() 做了 7 件事：
    评分 → 更新 ChildLevel → 更新 Child 统计 → Bookshelf 还书 →
    更新 Submission → 晋级检测 → 授予成就
    任何一个环节出错全部回滚，改一个功能要读懂十个服务。

  重构后（事件驱动）：
    QuizService.submit_answers() 只做两件事：
    1. 评分
    2. 发布 QuizPassedEvent
    其他域各自订阅事件自行响应。

  新增功能零改动：
    要加"测验通过后发送订阅消息"，
    只需 subscribe("quiz.passed", handler)，不改 QuizService 一行代码。

ADR-001 决策记录：
  为什么用同步事件总线而不是消息队列？
  1. DmkWords 用户量级（数千儿童），单进程 FastAPI 足够
  2. 同步事件在同一事务内完成，失败自动回滚，不需要补偿机制
  3. 引入消息队列会增加部署复杂度，当前阶段不值得
  4. 升级时机：定时任务执行时间超过 30 秒，或需要跨服务通信时

约束：
  - 事件处理器是同步的，优先在同一个 db session 事务内执行
  - 处理器抛出异常 → 事务回滚 → 所有操作回滚
  - publish(db=session) 时，处理器共享调用者的 session，不开新事务
  - publish(db=None) 时（如定时任务），处理器自行创建 session
  - 事件类型必须是字符串常量，不允许动态拼接
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class DomainEvent:
    """领域事件基类

    所有领域事件必须继承此类。
    event_type 用于匹配处理器，必须显式声明。
    """

    occurred_at: datetime = field(default_factory=datetime.now)
    event_type: str = ""


# ============================================================
# 核心领域事件定义
# 每个事件对应一个明确的业务动作，发布者和订阅者解耦
# ============================================================


@dataclass
class QuizPassedEvent(DomainEvent):
    """测验通过事件

    发布者：QuizService.submit_answers()
    订阅者：
      - advancement: 更新 ChildLevel.quizzes_passed_at_level
      - child: 更新 Child.total_words_read
      - bookshelf: 自动还书（V2 兼容，V3.1 后由 borrow 域处理）
      - borrow: 标记 BorrowRecord.quiz_passed=1（V3.1）
      - achievement: 检查是否授予成就
    """

    event_type: str = "quiz.passed"
    child_id: int = 0
    book_id: int = 0
    quiz_id: int = 0
    word_count: int = 0
    submission_id: int | None = None


@dataclass
class QuizFailedEvent(DomainEvent):
    """测验未通过事件"""

    event_type: str = "quiz.failed"
    child_id: int = 0
    book_id: int = 0
    quiz_id: int = 0
    score: float = 0.0


@dataclass
class BookReturnedEvent(DomainEvent):
    """还书事件

    发布者：BorrowService.return_book() 或 BookshelfService（V2兼容）
    订阅者：
      - book: 恢复 BookCopy 状态 + 释放库存
      - achievement: 检查"还书达人"成就
    """

    event_type: str = "book.returned"
    child_id: int = 0
    book_id: int = 0
    borrow_record_id: int | None = None
    book_copy_id: int | None = None
    reason: str = ""  # "quiz_passed" | "manual" | "overdue"


@dataclass
class BookBorrowedEvent(DomainEvent):
    """借书事件

    发布者：BorrowService.borrow_book()
    订阅者：
      - book: 更新 BookCopy 状态
      - achievement: 检查"阅读起步"成就
    """

    event_type: str = "book.borrowed"
    child_id: int = 0
    book_id: int = 0
    book_copy_id: int | None = None
    borrow_record_id: int | None = None


@dataclass
class LevelAdvancedEvent(DomainEvent):
    """晋级事件

    发布者：AdvancementService.check_and_advance()
    订阅者：
      - achievement: 授予晋级成就
      - certificate: 生成晋级证书
      - notification: 发送晋级通知
    """

    event_type: str = "level.advanced"
    child_id: int = 0
    from_level: str = ""
    to_level: str = ""


@dataclass
class OrderPaidEvent(DomainEvent):
    """订单支付成功事件

    发布者：OrderService.handle_payment_callback()
    订阅者：
      - child: 更新会员状态
      - deposit: 更新押金状态
      - notification: 发送支付成功通知
    """

    event_type: str = "order.paid"
    order_id: int = 0
    child_id: int = 0
    order_type: int = 0
    amount: Decimal = Decimal("0")


@dataclass
class CheckInEvent(DomainEvent):
    """打卡事件

    发布者：ReadingService.checkin()
    订阅者：
      - child: 更新连续打卡天数
      - achievement: 检查打卡成就
    """

    event_type: str = "reading.checkin"
    child_id: int = 0
    streak_days: int = 0


@dataclass
class DepositPaidEvent(DomainEvent):
    """押金支付成功事件

    发布者：DepositService.pay()
    订阅者：
      - child: 更新 deposit_status
    """

    event_type: str = "deposit.paid"
    child_id: int = 0
    deposit_id: int = 0
    amount: Decimal = Decimal("0")


@dataclass
class ReservationCreatedEvent(DomainEvent):
    """预约创建事件

    发布者：ReservationService.create()
    订阅者：
      - book: 锁定库存
    """

    event_type: str = "reservation.created"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReservationFulfilledEvent(DomainEvent):
    """预约取书事件

    发布者：ReservationService.fulfill()
    订阅者：
      - borrow: 创建借阅记录
      - book: 更新库存
    """

    event_type: str = "reservation.fulfilled"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0
    borrow_record_id: int | None = None
    book_copy_id: int | None = None  # B1a：扫码取书时定位到具体副本


@dataclass
class ReservationCancelledEvent(DomainEvent):
    """预约取消事件

    发布者：ReservationService.cancel_reservation()
    订阅者：
      - book: 释放库存
    """

    event_type: str = "reservation.cancelled"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReservationExpiredEvent(DomainEvent):
    """预约过期事件

    发布者：定时任务 reservation_expire.check()
    订阅者：
      - book: 释放库存
    """

    event_type: str = "reservation.expired"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReadingBookFinishedEvent(DomainEvent):
    """阅读完成一本书事件

    发布者：ReadingService.save_progress()
    订阅者：
      - advancement: increment_books_read + check_and_advance
    """

    event_type: str = "reading.book_finished"
    child_id: int = 0
    book_id: int = 0
    word_count: int = 0


@dataclass
class ReadingSessionCompletedEvent(DomainEvent):
    """阅读会话结束事件

    发布者：ReadingService.end_session()
    订阅者：
      - child: update_reading_stats (阅读时长累计)
    """

    event_type: str = "reading.session_completed"
    child_id: int = 0
    duration_minutes: int = 0


# ============================================================
# 事件总线实现
# ============================================================


class EventBus:
    """
    同步领域事件总线

    设计原则：
    - 同步执行（非异步），优先在同一事务内完成
    - publish(db=session) 时处理器共享 session，失败整体回滚
    - publish(db=None) 时处理器自行创建 session（如定时任务场景）
    - 处理器注册必须显式声明，不允许隐式耦合

    使用方式：
      # 注册（在 bootstrap.py 中）
      event_bus.subscribe("quiz.passed", handle_quiz_for_advancement)
      event_bus.subscribe("quiz.passed", handle_quiz_for_child_stats)

      # 发布（在 Service 中，共享事务）
      event_bus.publish(QuizPassedEvent(child_id=1, book_id=2), db=self.db)

      # 发布（在定时任务中，独立事务）
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件

        Args:
            event_type: 事件类型，如 "quiz.passed"
            handler: 处理器函数，签名 (event: DomainEvent, db: Optional[Session]) -> None
        """
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """取消订阅"""
        if handler in self._handlers.get(event_type, []):
            self._handlers[event_type].remove(handler)

    def publish(self, event: DomainEvent, db: Session | None = None) -> None:
        """发布事件

        同步调用所有已注册的处理器。
        处理器抛出异常时，重新抛出以触发事务回滚。

        Args:
            event: 领域事件实例
            db: 可选的数据库会话。传入时处理器共享同一事务；
                不传时处理器需自行创建 session（定时任务场景）

        Raises:
            Exception: 处理器中的异常会原样抛出
        """
        handlers = self._handlers.get(event.event_type, [])
        if not handlers:
            logger.debug(f"No handlers for event: {event.event_type}")
            return

        logger.info(
            f"Event published: {event.event_type}, "
            f"handlers: {len(handlers)}, "
            f"shared_db: {db is not None}, "
            f"data: {event.__dict__}"
        )

        if db is not None:
            # 共享 session 模式：handler 只做业务逻辑，异常自动 re-raise
            # L3-038：总线级记录失败订阅者（此前零日志，只看到最终 500 无法定位）
            for handler in handlers:
                try:
                    handler(event, db)
                except Exception:
                    logger.error(
                        f"Event handler failed (shared session): "
                        f"{event.event_type} -> {handler.__name__}",
                        exc_info=True,
                    )
                    raise
        else:
            # 独立 session 模式（定时任务）：每个 handler 独立事务
            from backend.database import get_session

            for handler in handlers:
                session = get_session()()
                try:
                    handler(event, session)
                    session.commit()
                except Exception:
                    session.rollback()
                    # 重试一次
                    try:
                        retry_session = get_session()()
                    except Exception as create_err:
                        logger.error(
                            f"Failed to create retry session: {create_err}",
                            exc_info=True,
                        )
                        self._record_dead_letter(event, handler.__name__, str(create_err))
                        continue
                    try:
                        handler(event, retry_session)
                        retry_session.commit()
                    except Exception as retry_err:
                        retry_session.rollback()
                        logger.error(
                            f"Event handler failed after retry (independent session): "
                            f"{event.event_type} -> {handler.__name__}: {retry_err}",
                            exc_info=True,
                        )
                        self._record_dead_letter(event, handler.__name__, retry_err)
                    finally:
                        try:
                            retry_session.close()
                        except Exception:
                            pass
                finally:
                    session.close()

    def _record_dead_letter(self, event: DomainEvent, handler_name: str, error: str) -> None:
        """记录死信事件到数据库"""
        try:
            from backend.database import get_session

            session = get_session()()
            try:
                import json

                from backend.common.dead_letter_model import DeadLetterEvent

                dl = DeadLetterEvent(
                    event_type=event.event_type,
                    event_data=json.dumps(event.__dict__, default=str),
                    handler_name=handler_name,
                    error_message=str(error)[:2000],
                )
                session.add(dl)
                session.commit()
                logger.warning(f"Dead letter recorded: {event.event_type} -> {handler_name}")
            finally:
                session.close()
        except Exception as dl_err:
            logger.error(f"Failed to record dead letter: {dl_err}", exc_info=True)

    def clear(self) -> None:
        """清空所有处理器（测试用）"""
        self._handlers.clear()


# 全局单例
event_bus = EventBus()
