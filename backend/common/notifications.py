# backend/common/notifications.py — 通知服务（WM11）
"""站内消息必达 + 微信订阅尽力送达 + 发送状态全记录。

- NotificationService.send(): 幂等写入站内消息 + 微信尽力发送；
- 事件 → 通知的订阅器在 backend/tasks/notify_handlers.py（公共编排层，避免 common 依赖业务域）；
- 微信通道：WECHAT_SUBSCRIBE_ENABLED=false 时记 skipped（通道未启用），
  模板 ID 缺失记 skipped，真实调用失败记 failed + 原因；站内消息永不因微信失败而丢。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from backend.common.events import DomainEvent
from backend.common.notification_models import Notification
from backend.config import get_settings

logger = logging.getLogger(__name__)

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
    """通知写入 + 微信尽力发送 + 幂等去重。"""

    def __init__(self, db: Session):
        self.db = db

    def _wechat_push(
        self,
        notification_id: int,
        title: str,
        content: str,
        openid: str | None,
        scene: str = "",
    ) -> None:
        """微信订阅尽力送达（Core UPDATE 回写状态，不碰 ORM 事务状态）。

        P2-24（2026-10-17 第六十九批·大审查）：此前把"**已入队**"当成"**已送达**"写库
        （`push_subscribe_message` 起线程后立刻 `return True` → 管理端渲染绿色「微信已送达」
        并进 Excel 导出，真实结果只在进程日志里）；反方向则把"标题未命中模板映射"写成
        `failed` + 假原因"额度不足/用户拒收"。现在三态如实：
        - **跳过**（通道未启用/无 openid/模板未配）→ `skipped` + **真实**原因；
        - **已提交**（线程已起、结果未知）→ `queued` + "等待微信回执"，线程拿到真实结果后
          **回写** sent/failed（见 `_writeback`，独立 session：请求事务早已结束）；
        - **异常** → `failed` + 真实异常文案。
        """
        settings = get_settings()
        if not getattr(settings, "WECHAT_SUBSCRIBE_ENABLED", False):
            self._set_wechat(notification_id, Notification.WECHAT_SKIPPED, "通道未启用")
            return
        if not openid:
            self._set_wechat(
                notification_id, Notification.WECHAT_SKIPPED, "家长未授权（无 openid）"
            )
            return
        try:
            from backend.integrations.wechat.subscribe import push_subscribe_message

            queued, reason = push_subscribe_message(
                openid,
                title,
                content,
                scene,
                on_result=lambda ok, err, status=None: self._writeback(
                    notification_id, ok, err, status
                ),
            )
        except Exception as exc:  # 网关异常不阻断站内消息
            logger.warning("wechat push failed: %s", exc)
            self._set_wechat(
                notification_id, Notification.WECHAT_FAILED, f"发送异常: {str(exc)[:200]}"
            )
            return
        if not queued:
            # 未入队 = **主动跳过**（模板未命中/未配置等），原因如实记录——不许伪装成"发送失败"
            self._set_wechat(notification_id, Notification.WECHAT_SKIPPED, reason)
            return
        self._set_wechat(notification_id, Notification.WECHAT_QUEUED, "已提交微信，等待回执")

    def _set_wechat(self, notification_id: int, status: str, error: str) -> None:
        self.db.execute(
            update(Notification)
            .where(Notification.id == notification_id)
            .values(wechat_status=status, wechat_error=error)
        )

    @staticmethod
    def _writeback(notification_id: int, ok: bool, error: str, status: str | None = None) -> None:
        """后台线程拿到微信真实结果后回写（**独立 session**：发起时的请求事务早已结束）。

        `status`（P1-5，2026-10-18）：43101（用户未订阅/已拒收）由发送线程显式传 `"skipped"`——
        它是"没送出去但也不是故障"，原先被当成功写成 `sent`（管理端显示绿色"已送达"）。
        """
        from backend.database import get_session

        try:
            session = get_session()
        except Exception as exc:  # noqa: BLE001 — 回写失败只记日志，绝不反向影响主流程
            logger.warning("wechat writeback 建会话失败：%s", exc)
            return
        try:
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
            logger.warning("wechat writeback 失败：%s", exc)
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
        self._wechat_push(new_id, title, content, openid, scene)
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
