# backend/common/notifications.py — 通知服务（WM11）
"""站内消息必达 + 微信订阅尽力送达 + 发送状态全记录。

- NotificationService.send(): 幂等写入站内消息；微信推送**在事务提交成功后**统一发出
  （P2-1，2026-10-31 第一百五十批：回滚不推送、提交恰好一次、推送失败不影响业务提交——
  此前是 INSERT 后立即推，调用方 commit 在其后，主事务回滚时微信已告知「付款成功」
  而站内信与业务状态回滚消失）；
- 事件 → 通知的订阅器在 backend/tasks/notify_handlers.py（公共编排层，避免 common 依赖业务域）；
- 微信通道：WECHAT_SUBSCRIBE_ENABLED=false 时记 skipped（通道未启用），
  模板 ID 缺失记 skipped，真实调用失败记 failed + 原因；站内消息永不因微信失败而丢。
"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import event, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from backend.common.events import DomainEvent
from backend.common.notification_models import Notification

logger = logging.getLogger(__name__)

# P2-1（2026-10-31 第一百五十批）：微信推送从「INSERT 后立即推」改为「事务提交后推」。
# 待发队列挂在 session.info（session 私有字典；请求/任务内单线程使用，无并发），
# after_commit 统一 dispatch、after_rollback 清空。
_PENDING_WECHAT_KEY = "pending_wechat_pushes"


@event.listens_for(Session, "after_commit")
def _flush_pending_wechat(session: Session) -> None:
    """提交成功 → 统一推送本事务积攒的微信消息（P2-1）。

    R-1（2026-10-31 复审 `docs/29`）：SAVEPOINT（`begin_nested`）的**提交**同样派发
    `after_commit`——此时外层事务尚未提交，行对独立 session 不可见；若照常 pop+dispatch
    会命中"行不存在就跳过"，队列被清空后外层真正提交时**永远不推**。用
    `in_nested_transaction()` 判别：SAVEPOINT 提交时保留队列，最外层 commit 统一 dispatch。

    为什么不能用 `in_transaction()`（复审原文按字面实现会让全部推送失效）：
    两轮探针实测 `after_commit` 派发期间 `in_transaction()` **恒为 True**（最外层 commit
    的钩子里也是 True——SQLAlchemy 在事件之后才收尾事务状态）；能区分两者的只有
    `in_nested_transaction()`（nested commit=True / outer commit=False）。

    dispatch 内部**吞一切异常**：走到这里事务已经提交成功，向调用方抛错只会让
    `commit()` 在成功之后报错——微信是尽力通道，落日志兜底即可。
    """
    if session.in_nested_transaction():
        return
    pending = session.info.pop(_PENDING_WECHAT_KEY, None)
    if pending:
        _dispatch_pending(pending)


@event.listens_for(Session, "after_rollback")
def _drop_pending_wechat_on_rollback(session: Session) -> None:
    """事务回滚 → 待发通知全部丢弃，**永不推送**（P2-1 的核心语义）。

    R-1（2026-10-31 复审）：SAVEPOINT 回滚**同样派发 `after_rollback`**（实测，此前
    注释宣称只走 `after_soft_rollback` 是错的）——此时**不能**清队：savepoint 之前
    入队的条目还活着（外层事务随后可能提交），savepoint 内已回滚的死条目由 dispatch
    的存在性校验兜住（行不存在 → 跳过不推）。只在最外层回滚时清空。
    """
    if session.in_nested_transaction():
        return
    pending = session.info.pop(_PENDING_WECHAT_KEY, None)
    if pending:
        logger.warning(
            "wechat push dropped: 事务回滚，%s 条待发通知不推送（ids=%s）",
            len(pending),
            [item[0] for item in pending],
        )


def _dispatch_pending(pending: list[tuple[int, str, str, str | None, str]]) -> None:
    """推送一个已提交事务积攒的全部微信消息（P2-1）。

    三条保证：
    - **行必须已提交且未软删才推**：一次 IN 查询过滤不存在的 id——savepoint 回滚的
      死条目、任何队列残留都推不出去（杜绝「给回滚掉的通知推微信」的幽灵推送）；
    - **初始状态用条件 UPDATE**（仅当 `wechat_status='none'`，见 `_set_wechat_if_untouched`）：
      dispatch 与微信回执回写（`_writeback`）是两个独立 session，回执是真实结果必须赢；
    - **独立 session + 末尾一次 commit**：状态写入不依赖、不污染任何业务事务；
      批量场景（过期提醒一次事务上千条）一次落库，不逐条开事务。
    """
    if not pending:
        return
    from backend.database import get_session

    db = get_session()
    try:
        ids = [item[0] for item in pending]
        existing = set(
            db.execute(
                select(Notification.id).where(
                    Notification.id.in_(ids), Notification.is_deleted == 0
                )
            ).scalars()
        )
        for notification_id, title, content, openid, scene in pending:
            if notification_id not in existing:
                logger.warning(
                    "wechat push skipped: 通知行 #%s 不存在（savepoint 回滚/已软删），不推送",
                    notification_id,
                )
                continue
            _dispatch_wechat(db, notification_id, title, content, openid, scene)
        db.commit()
    except Exception:  # noqa: BLE001 — 提交后副作用绝不反向影响调用方
        db.rollback()
        logger.exception("wechat dispatch 失败（%s 条，wechat_status 维持 none）", len(pending))
    finally:
        db.close()


def _dispatch_wechat(
    db: Session,
    notification_id: int,
    title: str,
    content: str,
    openid: str | None,
    scene: str = "",
) -> None:
    """单条微信订阅尽力送达（判定与状态三态如实；P2-1 起在事务提交后调用）。

    P2-24（2026-10-17 第六十九批·大审查）：此前把"**已入队**"当成"**已送达**"写库
    （`push_subscribe_message` 起线程后立刻 `return True` → 管理端渲染绿色「微信已送达」
    并进 Excel 导出，真实结果只在进程日志里）；反方向则把"标题未命中模板映射"写成
    `failed` + 假原因"额度不足/用户拒收"。现在三态如实：
    - **跳过**（通道未启用/无 openid/模板未配/场景未映射）→ `skipped` + **真实**原因；
    - **已提交**（线程池已受理、结果未知）→ `queued` + "等待微信回执"，worker 拿到真实
      结果后**回写** sent/failed/skipped（见 `_writeback`，独立 session）；
    - **异常/队列满** → `failed` + 真实原因。

    P2-42（2026-10-20 第八轮）：判定与并发都在订阅模块（`precheck_subscribe` 单一事实源
    + 有界线程池）；本方法只负责把订阅模块给出的**状态与原因如实落库**——第三项返回值
    就是"该记什么状态"，不再由本方法二次判断。
    """
    try:
        from backend.integrations.wechat.subscribe import push_subscribe_message

        queued, reason, hint = push_subscribe_message(
            openid,
            title,
            content,
            scene,
            on_result=lambda ok, err, status=None: NotificationService._writeback(
                notification_id, ok, err, status
            ),
        )
    except Exception as exc:  # 网关异常不阻断站内消息
        logger.warning("wechat push failed: %s", exc)
        _set_wechat_if_untouched(
            db, notification_id, Notification.WECHAT_FAILED, f"发送异常: {str(exc)[:200]}"
        )
        return
    if not queued:
        # 未入队：状态与原因都来自订阅模块（skipped=主动跳过；failed=本该发没发成）
        _set_wechat_if_untouched(db, notification_id, hint or Notification.WECHAT_SKIPPED, reason)
        return
    _set_wechat_if_untouched(
        db, notification_id, Notification.WECHAT_QUEUED, "已提交微信，等待回执"
    )


def _is_mysql_deadlock(exc: BaseException) -> bool:
    """MySQL 错误码 1213（InnoDB 死锁）：服务端已回滚**整个事务**，不是语句级失败。"""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", None)
    return bool(args) and args[0] == 1213


def _set_wechat_if_untouched(db: Session, notification_id: int, status: str, error: str) -> None:
    """写微信通道初始状态——**仅当行仍是 `none`**（P2-1 引入的条件写）。

    dispatch 与 `_writeback`（回执回写）是两个独立 session，没有同一把事务锁：
    极端时序下回执可能先于 dispatch 的初始状态落库（真实结果），无条件 UPDATE 会
    把 `sent`/`failed` 覆盖回 `queued` 且永远无人再改。条件写让「后到的旧事实」
    自动失效：初始状态只补空白，不覆盖任何已知结果。
    （Core UPDATE + `synchronize_session=False`，同原 `_set_wechat`——不碰 ORM 身份映射。）"""
    db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.wechat_status == Notification.WECHAT_NONE,
        )
        .values(wechat_status=status, wechat_error=error)
        .execution_options(synchronize_session=False)
    )


def _record_writeback_dead_letter(notification_id: int, error: object) -> None:
    """微信回写失败 → 落一条死信（B8，第九轮审查 P3 族）。

    为什么必须有：回写失败原先只有一行日志，通知行的 `wechat_status` 永久停在 `queued`
    ——「微信通道没写回」这件事在运营可见面上**不存在**（死信看板的语义正是"跨域副作用没发生"）。
    落库用**独立 session**且整体吞异常：死信是兜底，不允许自己变成故障也不会影响调用方。
    """
    try:
        from backend.common.notification_models import DeadLetter
        from backend.database import get_session

        session = get_session()
        try:
            session.add(
                DeadLetter(
                    event_type="notification.wechat_writeback",
                    handler_name="_writeback",
                    error=str(error)[:2000],
                    payload=json.dumps({"notification_id": notification_id}, ensure_ascii=False),
                    retry_count=0,
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("死信落库失败（wechat writeback，通知 #%s）", notification_id)


#: 回写等待"通知行可见"的上限与轮询间隔（P2-42，2026-10-20 第八轮大审查）。
#:
#: **实测根因**（不是静态推断）：通知行由**发起方事务**插入，而微信回执往往在几百毫秒内就
#: 回来——此时发起方事务可能还没提交。对**未提交行**发 UPDATE 不是"匹配 0 行"，而是
#: InnoDB **等锁**：探针实测 `1205 Lock wait timeout exceeded` 要等满 50.15 秒才返回，
#: 期间白占一条连接池连接；回写线程随后只记一条 warning → `wechat_status` **永久停在
#: `queued`**（管理端一直显示"等待回执"）。千条提醒就是千条这样的线程/连接。
#: 现在改成**轮询可见性**：快照读（不阻塞、不加锁）确认行已提交后再 UPDATE。
WRITEBACK_VISIBLE_TIMEOUT_SECONDS = 30.0
WRITEBACK_POLL_SECONDS = 0.25

# 场景常量（与 notification_models.Notification.scene 一一对应）
SCENE_MONEY_ORDER_PAID = "money.order_paid"
SCENE_MONEY_REFUND_RESULT = "money.refund_result"
SCENE_MONEY_REFUND_RECEIVED = "money.refund_received"
SCENE_MONEY_REFUND_FAILED = "money.refund_failed"
SCENE_MONEY_DEPOSIT_PAID = "money.deposit_paid"
SCENE_BORROW_SUCCESS = "borrow.success"
SCENE_BORROW_RETURNED = "borrow.returned"
SCENE_BORROW_DUE_REMIND = "borrow.due_remind"
SCENE_BORROW_OVERDUE = "borrow.overdue"
SCENE_READING_QUIZ_RESULT = "reading.quiz_result"
SCENE_READING_MILESTONE = "reading.milestone"
SCENE_READING_LEVEL_UP = "reading.level_up"
SCENE_MEMBER_EXPIRE_REMIND = "member.expire_remind"
SCENE_MEMBER_WITHDRAW_RESULT = "member.withdraw_result"
SCENE_MEMBER_PENDING_EVAL = "member.pending_eval"
SCENE_ACTIVITY_ENROLL = "activity.enroll"
SCENE_ACTIVITY_REMIND = "activity.remind"
SCENE_ACTIVITY_CANCEL = "activity.cancel"
SCENE_RESERVATION_CREATED = "reservation.created"
SCENE_RESERVATION_EXPIRING = "reservation.expiring"
SCENE_RESERVATION_RELEASED = "reservation.released"
# G5（2026-10-14 第六十轮）：预约期间副本转维护/遗失、**无同书在馆副本可换** → 预约转异常态。
# PRD §5.5「没有其他副本了，预约转『异常』状态并通知家长和馆员」（§八 预约类通知第三项）。
SCENE_RESERVATION_EXCEPTION = "reservation.exception"
SCENE_REPORT_GENERATED = "report.generated"
SCENE_OTHER_EVALUATION_UPLOADED = "other.evaluation_uploaded"
SCENE_OTHER_TRANSFER_RESULT = "other.transfer_result"
SCENE_CIRCLE_LIKED = "circle.liked"


class NotificationService:
    """通知写入 + 微信尽力发送（事务提交后） + 幂等去重。"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def existing_keys(
        db: Session, *, scene: str, ref_type: str, ref_ids: list[int]
    ) -> set[tuple[str, str, int]]:
        """预取"已发过"的 `(ref_id, dedup_key, parent_id)` 三元组（**一次 IN 查询**，P2-41）。

        为什么要有它（2026-10-20 第八轮）：批量提醒任务每轮都会重新扫到**同一批存量记录**
        （窗口是"未来 N 天内到期"，不是"新记录"）。若不做预取，每条都要走 `send()` 内部的
        存在性 SELECT——6000 条存量 × 每小时一轮 = 每小时 6000 条无用查询（逾期链路 300 秒
        一轮，更密）。预取让稳态开销回到"一次 IN 查询 + 只对新记录写通知"。

        **为什么必须带 `parent_id`**（不是 `(ref_id, dedup_key)` 两元组）：唯一索引是
        `(parent_id, scene, ref_type, ref_id, dedup_key, is_deleted)`，而 `ref_id` 并不总是
        "一对一"——活动提醒的 `ref_id` 是**活动 id**，一场活动有多个家长。只按两元组预取会把
        "别的家长已发过"误判成"这个家长已发过"→ **漏发**（假绿）。三元组才与唯一索引同口径。
        """
        ids = [str(i) for i in set(ref_ids)]
        if not ids:
            return set()
        rows = (
            db.query(Notification.ref_id, Notification.dedup_key, Notification.parent_id)
            .filter(
                Notification.scene == scene,
                Notification.ref_type == ref_type,
                Notification.ref_id.in_(ids),
                Notification.is_deleted == 0,
            )
            .all()
        )
        return {(r[0], r[1], r[2]) for r in rows}

    @staticmethod
    def _writeback(notification_id: int, ok: bool, error: str, status: str | None = None) -> None:
        """后台 worker 拿到微信真实结果后回写（**独立 session**：发起时的请求事务早已结束）。

        `status`（P1-5，2026-10-18）：43101（用户未订阅/已拒收）由发送 worker 显式传 `"skipped"`——
        它是"没送出去但也不是故障"，原先被当成功写成 `sent`（管理端显示绿色"已送达"）。

        P2-42（2026-10-20 第八轮）：**先等行可见，再 UPDATE**。通知行由发起方事务插入，
        回执常在提交前就回来；对未提交行直接 UPDATE 会**等锁 50 秒**（实测 1205）并白占连接，
        最终状态永久停在 `queued`。这里用快照读轮询（不加锁、不阻塞），
        每次轮询后 `rollback()` 把连接**还回池里**（不占着连接睡觉）。
        超时（事务始终未提交/已回滚）如实记 warning，绝不无限等。
        """
        from backend.database import get_session

        try:
            session = get_session()
        except Exception as exc:  # noqa: BLE001 — 回写失败只记日志，绝不反向影响主流程
            logger.warning("wechat writeback 建会话失败：%s", exc)
            return
        try:
            deadline = time.monotonic() + WRITEBACK_VISIBLE_TIMEOUT_SECONDS
            while True:
                visible = (
                    session.query(Notification.id)
                    .filter(Notification.id == notification_id)
                    .first()
                    is not None
                )
                session.rollback()  # 结束只读事务：连接回池，轮询期间不占连接
                if visible:
                    break
                if time.monotonic() >= deadline:
                    logger.error(
                        "wechat writeback 放弃：通知行 #%s 在 %.0f 秒内未提交"
                        "（发起方事务未提交或已回滚），状态维持 queued/none",
                        notification_id,
                        WRITEBACK_VISIBLE_TIMEOUT_SECONDS,
                    )
                    return
                time.sleep(WRITEBACK_POLL_SECONDS)
            session.execute(
                update(Notification)
                .where(Notification.id == notification_id)
                .values(
                    wechat_status=(
                        Notification.WECHAT_SKIPPED
                        if status == "skipped"
                        else (Notification.WECHAT_SENT if ok else Notification.WECHAT_FAILED)
                    ),
                    wechat_error="" if (ok and status != "skipped") else error,
                )
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            # B8（第九轮审查 P3 族）：原先只 warning 一行——通知行的 `wechat_status` 会**永远停在
            # `queued`**，运营在日志/看板里都看不见"这条微信通道没写回"。改 error + 落死信
            # （死信看板语义就是"跨域副作用没发生"，见 `admin/dead_letter_service`）。
            logger.error(
                "wechat writeback 失败（通知 #%s，wechat_status 维持 queued）：%s",
                notification_id,
                exc,
            )
            _record_writeback_dead_letter(notification_id, exc)
        finally:
            try:
                session.close()
            except Exception:
                pass

    def send(
        self,
        *,
        parent_id: int,
        scene: str,
        title: str,
        content: str,
        category: str,
        child_id: int | None = None,
        ref_type: str = "",
        ref_id: str = "",
        dedup_key: str = "1",
        openid: str | None = None,
    ) -> bool:
        """写入站内消息并尽力发微信。返回是否为新写入（False=重复已存在）。

        并发安全：MySQL INSERT IGNORE（宪法 MySQL-only）撞唯一约束静默忽略
        （rowcount=0），零异常、零 ORM flush、不污染事务——EventBus 同步共享
        事务下不会连坐回滚主业务写入（审查 P0 修复）。

        P2-1（2026-10-31 第一百五十批）：微信推送**不在本方法内发生**——调用方的
        `commit()` 在其后，立即推会在「主事务回滚」时留下已发出的「付款成功/退款到账」。
        新写入的行记入 `session.info` 待发队列，由模块级 `after_commit` 监听器在
        提交成功后统一 dispatch（回滚则由 `after_rollback` 丢弃，永不推送）。
        """
        exists = (
            self.db.query(func.count(Notification.id))
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == scene,
                Notification.ref_type == ref_type,
                Notification.ref_id == ref_id,
                Notification.dedup_key == dedup_key,
                Notification.is_deleted == 0,
            )
            .scalar()
        )
        if exists:
            return False

        stmt = mysql_insert(Notification).values(
            parent_id=parent_id,
            child_id=child_id,
            scene=scene,
            category=category,
            title=title,
            content=content,
            ref_type=ref_type,
            ref_id=ref_id,
            dedup_key=dedup_key,
            wechat_status=Notification.WECHAT_NONE,
            wechat_error="",
        )
        result = self.db.execute(stmt.prefix_with("IGNORE"))
        if result.rowcount == 0:
            return False  # 并发窗口撞唯一索引，已由他事务写入
        new_id = result.inserted_primary_key[0]
        # P2-1：只入队不推送——commit 成功后由 after_commit 监听器统一 dispatch，
        # 回滚时由 after_rollback 丢弃。通知行状态初始为 none（行可见前的中间态）。
        pending = self.db.info.setdefault(_PENDING_WECHAT_KEY, [])
        pending.append((new_id, title, content, openid, scene))
        return True

    def send_event(
        self,
        event: DomainEvent,
        *,
        parent_id: int,
        scene: str,
        title: str,
        content: str,
        category: str,
        child_id: int | None = None,
        ref_type: str = "",
        ref_id: str = "",
        dedup_key: str = "1",
        openid: str | None = None,
    ) -> None:
        """事件订阅器专用：不抛异常（通知失败不阻断业务事务）。

        `dedup_key`（2026-10-15 大审查 P1 #2）：默认 "1" =「同一家长 × 场景 × 对象**终身一条**」，
        对"每次提交后都要发"的重复事件（测验成绩/等级升级/里程碑）是错的——第 2 次起会被
        唯一索引静默吞掉（若第一次是"未通过"，之后的"通过"也永远发不出）。
        重复事件必须传业务对象键，见 `tasks/notify_handlers.py` 三个处理器的取键口径。
        """
        try:
            self.send(
                parent_id=parent_id,
                scene=scene,
                title=title,
                content=content,
                category=category,
                child_id=child_id,
                ref_type=ref_type,
                ref_id=ref_id,
                dedup_key=dedup_key,
                openid=openid,
            )
        except Exception as exc:
            if _is_mysql_deadlock(exc):
                # B1（2026-10-31 第一百五十一批）：死锁时 InnoDB 已回滚**整个事务**——这里吞掉
                # 会让调用方 commit() 静默成功、此前 flush 过的业务写入全部丢失（订单写成功的
                # 假象）。必须 re-raise：EventBus 共享事务模式（events.publish）会 dead-letter
                # 后原样上抛，业务调用方可见失败 → 回滚重试。普通失败（唯一键/网关）仍吞
                # （通知尽力语义不变；这层吞的是"通知发不出"，不是"事务已死"）。
                raise
            logger.error("notification send failed for event=%s: %s", event.event_type, exc)

    def list_mine(
        self,
        parent_id: int,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        scene: str | None = None,
    ) -> dict:
        """家长端消息中心（A-1/T6 下沉）。

        fix34 R0：`scene` 过滤（如 circle.liked）——阅读圈顶部「谁赞了你」通知条
        只拉本场景，避免混在「其他」分类里被分页挤出首页。
        """
        unread = (
            self.db.query(func.count(Notification.id))
            .filter(
                Notification.parent_id == parent_id,
                Notification.is_deleted == 0,
                Notification.read_at.is_(None),
            )
            .scalar()
            or 0
        )
        q = self.db.query(Notification).filter(
            Notification.parent_id == parent_id, Notification.is_deleted == 0
        )
        if category:
            q = q.filter(Notification.category == category)
        if scene:
            q = q.filter(Notification.scene == scene)
        total = q.count()
        rows = (
            q.order_by(Notification.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        )
        # WM15-C1：分场景未读（阅读圈 tab 红点=circle_liked，**同表同源**，非第二套计数）
        unread_rows = (
            self.db.query(Notification.scene, func.count(Notification.id))
            .filter(
                Notification.parent_id == parent_id,
                Notification.is_deleted == 0,
                Notification.read_at.is_(None),
            )
            .group_by(Notification.scene)
            .all()
        )
        return {
            "unread": unread,
            "unread_by_scene": {(k or "").replace(".", "_"): int(v) for k, v in unread_rows},
            "total": total,
            "items": [
                {
                    "id": n.id,
                    "category": n.category,
                    "scene": n.scene,
                    "title": n.title,
                    "content": n.content,
                    "read": n.is_read,
                    # WM15-A1：深链数据面（此前库里存了但没暴露给小程序的 API）
                    "ref_type": n.ref_type or "",
                    "ref_id": n.ref_id or "",
                    "created_at": n.create_time.strftime("%Y-%m-%d %H:%M") if n.create_time else "",
                }
                for n in rows
            ],
        }

    def mark_read(self, parent_id: int, ids: list[int], all_: bool = False) -> int:
        """标记已读（A-1/T6 下沉）：all 全读或按 ids；返回标记条数。"""
        from datetime import datetime

        q = self.db.query(Notification).filter(
            Notification.parent_id == parent_id,
            Notification.is_deleted == 0,
            Notification.read_at.is_(None),
        )
        if not all_:
            q = q.filter(Notification.id.in_(ids))
        rows = q.all()
        for n in rows:
            n.read_at = datetime.now()
        self.db.commit()
        return len(rows)
