# backend/domain/identity/wm_notify.py — 退会/退款审核结果的家长通知（WM11）
"""wm10_service 已 800 行上限，审核结果通知收敛到此（域内纯通知辅助）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_MEMBER_WITHDRAW_RESULT,
    SCENE_MONEY_REFUND_FAILED,
    SCENE_MONEY_REFUND_RECEIVED,
    SCENE_MONEY_REFUND_RESULT,
    NotificationService,
)


def notify_refund_reviewed(db: Session, child, req, approve: bool, remark: str) -> None:
    """退款审核结果（通过/拒绝）通知家长。"""
    NotificationService(db).send(
        parent_id=child.parent_id,
        scene=SCENE_MONEY_REFUND_RESULT,
        title="退款审核结果",
        content=(
            f"退款申请（{req.amount} 元）已审核通过，等待退款到账。"
            if approve
            else f"退款申请（{req.amount} 元）未通过审核：{remark}"
        ),
        category=Notification.CATEGORY_MONEY,
        child_id=child.id,
        ref_type="refund_request",
        ref_id=str(req.id),
    )


def notify_refund_executed(db: Session, child, req, success: bool, remark: str) -> None:
    """退款到账 / 退款失败通知家长。"""
    if success:
        scene, title = SCENE_MONEY_REFUND_RECEIVED, "退款到账"
        content = f"退款 {req.amount} 元已到账（{remark or '人工打款登记'}）。"
    else:
        scene, title = SCENE_MONEY_REFUND_FAILED, "退款失败"
        content = f"退款 {req.amount} 元执行失败：{remark}。可联系馆员处理。"
    NotificationService(db).send(
        parent_id=child.parent_id,
        scene=scene,
        title=title,
        content=content,
        category=Notification.CATEGORY_MONEY,
        child_id=child.id,
        ref_type="refund_request",
        ref_id=str(req.id),
    )


def notify_withdrawal_reviewed(
    db: Session, child, request_id: int, approve: bool, remark: str
) -> None:
    """退会审核结果通知家长。"""
    NotificationService(db).send(
        parent_id=child.parent_id,
        scene=SCENE_MEMBER_WITHDRAW_RESULT,
        title="退会审核结果",
        content=(
            "退会申请已通过，进入结算流程（退款单将逐笔处理）。"
            if approve
            else f"退会申请未通过：{remark}"
        ),
        category=Notification.CATEGORY_MEMBER,
        child_id=child.id,
        ref_type="withdrawal_request",
        ref_id=str(request_id),
    )
