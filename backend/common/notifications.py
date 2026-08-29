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
SCENE_RESERVATION_EXPIRING = "reservation.expiring"
SCENE_RESERVATION_RELEASED = "reservation.released"
SCENE_REPORT_GENERATED = "report.generated"
SCENE_OTHER_EVALUATION_UPLOADED = "other.evaluation_uploaded"
SCENE_OTHER_TRANSFER_RESULT = "other.transfer_result"


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
    ) -> None:
        """微信订阅尽力送达（Core UPDATE 回写状态，不碰 ORM 事务状态）。"""
        try:
            settings = get_settings()
            if not getattr(settings, "WECHAT_SUBSCRIBE_ENABLED", False):
                status, error = Notification.WECHAT_SKIPPED, "通道未启用"
            elif not openid:
                status, error = Notification.WECHAT_SKIPPED, "家长未授权（无 openid）"
            else:
                from backend.integrations.wechat.subscribe import push_subscribe_message

                ok = push_subscribe_message(openid, title, content)
                if ok:
                    status, error = Notification.WECHAT_SENT, ""
                else:
                    status, error = (
                        Notification.WECHAT_FAILED,
                        "微信订阅发送失败（额度不足/用户拒收等）",
                    )
        except Exception as exc:  # 网关异常不阻断站内消息
            logger.warning("wechat push failed: %s", exc)
            status, error = Notification.WECHAT_FAILED, f"发送异常: {str(exc)[:200]}"
        self.db.execute(
            update(Notification)
            .where(Notification.id == notification_id)
            .values(wechat_status=status, wechat_error=error)
        )

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
        self._wechat_push(new_id, title, content, openid)
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
        openid: str | None = None,
    ) -> None:
        """事件订阅器专用：不抛异常（通知失败不阻断业务事务）。"""
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
                openid=openid,
            )
        except Exception as exc:
            logger.error("notification send failed for event=%s: %s", event.event_type, exc)
