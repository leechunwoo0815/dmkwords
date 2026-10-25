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

import json
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

    发布者：`growth/quiz_events.py`（测验提交后发布）
    订阅者：
      - `tasks/notify_handlers.py::_on_quiz_passed`（站内信 / 微信模板消息）
    W2-D2（第十轮 P3，2026-10-25）订正：原文列的 advancement/child/bookshelf/borrow/
    achievement 五个订阅者**全仓都不存在**，`ChildLevel.quizzes_passed_at_level` 这个符号
    也不存在——照它找接线点会白找一遍。现在只写核实过的真实订阅者。
    """

    event_type: str = "quiz.passed"
    child_id: int = 0
    book_id: int = 0
    quiz_id: int = 0
    word_count: int = 0
    submission_id: int | None = None


@dataclass
class QuizFailedEvent(DomainEvent):
    """测验未通过事件

    发布者：`growth/quiz_events.py`（测验提交后发布）
    订阅者：
      - `tasks/notify_handlers.py::_on_quiz_failed`（站内信 / 微信模板消息）
    """

    event_type: str = "quiz.failed"
    child_id: int = 0
    book_id: int = 0
    quiz_id: int = 0
    score: float = 0.0
    # P1 #2（2026-10-15 大审查）：通知去重键挂在**业务提交记录**上（QuizAttempt.id），
    # 与 QuizPassedEvent 对齐——默认键 "1" 下同一孩子第 2 次起的成绩通知全被静默吞掉。
    submission_id: int | None = None


@dataclass
class BookReturnedEvent(DomainEvent):
    """还书事件

    发布者：`circulation/service.py::CirculationService`（还书时发布）
    订阅者：
      - `tasks/notify_handlers.py::_on_book_returned`（站内信 / 微信模板消息）
    W2-D2（第十轮 P3，2026-10-25）订正：原文的 `BorrowService`/`BookshelfService` 与
    "book/achievement 订阅者"全仓不存在。
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

    发布者：`circulation/service.py::CirculationService`（借出时发布）
    订阅者：
      - `tasks/notify_handlers.py::_on_book_borrowed`（站内信 / 微信模板消息）
    W2-D2（第十轮 P3，2026-10-25）订正：原文的 `BorrowService` 与"book/achievement 订阅者"
    全仓不存在（真实写口是 `CirculationService`，库存状态在**同事务**里改，不走事件）。
    """

    event_type: str = "book.borrowed"
    child_id: int = 0
    book_id: int = 0
    book_copy_id: int | None = None
    borrow_record_id: int | None = None


@dataclass
class LevelAdvancedEvent(DomainEvent):
    """晋级事件

    发布者：`growth/service.py`（等级重算后发布，见 `_recalc_level` 调用点）
    订阅者：
      - `tasks/notify_handlers.py::_on_level_up`（站内信 / 微信模板消息）
    W2-D2（第十轮 P3，2026-10-25）订正：原文的 `AdvancementService.check_and_advance` 与
    "achievement/certificate 订阅者"全仓不存在（晋级成就与证书都随等级同事务落库）。
    """

    event_type: str = "level.advanced"
    child_id: int = 0
    from_level: str = ""
    to_level: str = ""


@dataclass
class MilestoneAchievedEvent(DomainEvent):
    """里程碑达成事件（WM11 新增：勋章达成即时通知家长与馆员）

    发布者：`growth/service.py`（里程碑判定）
    订阅者：
      - `tasks/notify_handlers.py::_on_milestone_achieved`
    W2-D2（第十轮 P3，2026-10-25）核实：原文的订阅者清单与实现不符，现按真实订阅者订正（同批核对：事件面 16 类 / 发布 12 / 订阅 12）。
    """

    event_type: str = "milestone.achieved"
    child_id: int = 0
    nodes: tuple = ()


@dataclass
class OrderPaidEvent(DomainEvent):
    """订单支付成功事件

    发布者：`OrderService`（人工确认收款 / 线上回调结算）
    订阅者：
      - `tasks/notify_handlers.py::_on_order_paid`
    W2-D2（第十轮 P3，2026-10-25）核实：原文的订阅者清单与实现不符，现按真实订阅者订正（同批核对：事件面 16 类 / 发布 12 / 订阅 12）。
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
    订阅者：growth 打卡周期积分（`growth/growth_handlers.py::_handle_checkin`
    以**字符串字面量** `"reading.checkin"` 订阅，`main.py` 启动时 register_growth_handlers 注册）
    → 调 `GrowthService.on_checkin()` 发周期奖（每 7 天 +10 / 每 30 天 +50）。

    2026-10-24（第九轮 E 批·N-7）订正：原 docstring 写「订阅者：child 更新连续打卡天数 /
    achievement 检查打卡成就」——全仓无这两处订阅，**是谎报**；streak 与成就走同步直调，
    本事件的唯一订阅者是 growth 周期积分。
    """

    event_type: str = "reading.checkin"
    child_id: int = 0
    streak_days: int = 0


@dataclass
class DepositPaidEvent(DomainEvent):
    """押金支付成功事件

    发布者：`billing/service.py`（押金收款确认）
    订阅者：
      - `tasks/notify_handlers.py::_on_deposit_paid`
    W2-D2（第十轮 P3，2026-10-25）核实：原文的订阅者清单与实现不符，现按真实订阅者订正（同批核对：事件面 16 类 / 发布 12 / 订阅 12）。
    """

    event_type: str = "deposit.paid"
    child_id: int = 0
    deposit_id: int = 0
    amount: Decimal = Decimal("0")
    # 第八轮 P2-11：付款订单号 + 首缴/补缴标记。`deposit_id` 在**补缴**时不变
    # （`deposits.child_id` 单列唯一），拿它当通知身份会让第二次缴款被唯一索引吞掉。
    order_id: int = 0
    is_supplement: bool = False


@dataclass
class ReservationCreatedEvent(DomainEvent):
    """预约创建事件

    发布者：`reading/reservation_service.py::ReservationService.create`
    订阅者：
      - `tasks/notify_handlers.py::_on_reservation_created`
    W2-D2（第十轮 P3，2026-10-25）核实：原文的订阅者清单与实现不符，现按真实订阅者订正（同批核对：事件面 16 类 / 发布 12 / 订阅 12）。
    """

    event_type: str = "reservation.created"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReservationFulfilledEvent(DomainEvent):
    """预约取书事件

    发布者：无（当前无发布点）
    订阅者：无
    W2-D2（第十轮 P3，2026-10-25）核实：**本事件当前既无发布者也无订阅者**（未接线事件，清单见 `docs/09` 挂账「N-8 四个死事件类」）。
    保留类定义是为了不阻断「先定契约后接线」的写法；接线或删除由甲方裁定。

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

    发布者：无（当前无发布点；取消路径**同事务**直接释放副本，不走事件）
    订阅者：无
    W2-D2（第十轮 P3，2026-10-25）核实：**本事件当前既无发布者也无订阅者**（未接线事件，清单见 `docs/09` 挂账「N-8 四个死事件类」）。
    保留类定义是为了不阻断「先定契约后接线」的写法；接线或删除由甲方裁定。

    """

    event_type: str = "reservation.cancelled"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReservationExpiredEvent(DomainEvent):
    """预约过期事件

    发布者：`reading/reservation_service.py::ReservationService.expire_due`
    订阅者：
      - `tasks/notify_handlers.py::_on_reservation_released`
    W2-D2（第十轮 P3，2026-10-25）核实：原文的订阅者清单与实现不符，现按真实订阅者订正（同批核对：事件面 16 类 / 发布 12 / 订阅 12）。
    """

    event_type: str = "reservation.expired"
    child_id: int = 0
    book_id: int = 0
    reservation_id: int = 0


@dataclass
class ReadingBookFinishedEvent(DomainEvent):
    """阅读完成一本书事件

    发布者：无（当前无发布点）
    订阅者：无
    W2-D2（第十轮 P3，2026-10-25）核实：**本事件当前既无发布者也无订阅者**（未接线事件，清单见 `docs/09` 挂账「N-8 四个死事件类」）。
    保留类定义是为了不阻断「先定契约后接线」的写法；接线或删除由甲方裁定。

    """

    event_type: str = "reading.book_finished"
    child_id: int = 0
    book_id: int = 0
    word_count: int = 0


@dataclass
class ReadingSessionCompletedEvent(DomainEvent):
    """阅读会话结束事件

    发布者：无（当前无发布点）
    订阅者：无
    W2-D2（第十轮 P3，2026-10-25）核实：**本事件当前既无发布者也无订阅者**（未接线事件，清单见 `docs/09` 挂账「N-8 四个死事件类」）。
    保留类定义是为了不阻断「先定契约后接线」的写法；接线或删除由甲方裁定。

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
                except Exception as exc:
                    logger.error(
                        f"Event handler failed (shared session): "
                        f"{event.event_type} -> {handler.__name__}",
                        exc_info=True,
                    )
                    # P2-18（2026-10-17 第六十九批·大审查）：共享事务**会被调用方回滚**
                    # （业务一致性优先，此处不能吞异常——那会留下半个事务），但失败事件必须
                    # 留下**能活过回滚**的痕迹：用独立 session 落 `dead_letters` 后再 re-raise。
                    # 此前本分支零落库（且注释误称"新项目无死信表"）→ 生产里死信表**永远 0 行**，
                    # 订阅器失败只剩进程日志（重启即丢）。
                    self._record_dead_letter(event, handler.__name__, exc, retry_count=0)
                    raise
        else:
            # 独立 session 模式（定时任务）：每个 handler 独立事务
            from backend.database import get_session

            for handler in handlers:
                session = get_session()
                try:
                    handler(event, session)
                    session.commit()
                except Exception:
                    session.rollback()
                    # 重试一次
                    try:
                        retry_session = get_session()
                    except Exception as create_err:
                        logger.error(
                            f"Failed to create retry session: {create_err}",
                            exc_info=True,
                        )
                        self._record_dead_letter(event, handler.__name__, create_err, retry_count=0)
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
                        self._record_dead_letter(event, handler.__name__, retry_err, retry_count=1)
                    finally:
                        try:
                            retry_session.close()
                        except Exception:
                            pass
                finally:
                    session.close()

    def _record_dead_letter(
        self, event: DomainEvent, handler_name: str, error: object, *, retry_count: int = 0
    ) -> None:
        """死信落库 + 结构化日志（`dead_letters` 表；D6 正式形态）。

        P2-18（2026-10-17 第六十九批·大审查）：本方法此前**只写日志**，注释还写着
        "新项目无死信表"——而模型（`common/notification_models.DeadLetter`）与迁移
        （`f7a2c9e3b5d4`）**都在**，且共享事务分支从不调用它 → 生产死信表零写入。
        现在两条分支都落库：共享分支记 `retry_count=0`（业务事务一致回滚，不重试），
        `db=None` 分支在"重试一次仍失败"后记 `retry_count=1`。

        落库必须用**独立 session**（共享 session 马上要回滚）且**绝不允许影响原异常**：
        建会话/写库任何失败都吞掉并记日志——死信是兜底，不能自己变成故障。
        """
        payload = json.dumps(
            {k: str(v)[:200] for k, v in event.__dict__.items()}, ensure_ascii=False
        )
        message = str(error)[:2000]
        logger.error(
            "Dead letter: event=%s handler=%s retry_count=%s error=%s data=%s",
            event.event_type,
            handler_name,
            retry_count,
            message,
            payload,
        )
        try:
            from backend.common.notification_models import DeadLetter
            from backend.database import get_session

            session = get_session()
        except Exception as exc:  # noqa: BLE001 — 死信落库失败不能盖住原异常
            logger.error("死信落库不可用（建会话失败）：%s", exc)
            return
        try:
            session.add(
                DeadLetter(
                    event_type=event.event_type,
                    handler_name=handler_name,
                    payload=payload,
                    error=message,
                    retry_count=retry_count,
                )
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 — 同上，只记日志
            session.rollback()
            logger.error("死信落库失败：%s", exc)
        finally:
            try:
                session.close()
            except Exception:
                pass

    def clear(self) -> None:
        """清空所有处理器（测试用）"""
        self._handlers.clear()


# 全局单例
event_bus = EventBus()
