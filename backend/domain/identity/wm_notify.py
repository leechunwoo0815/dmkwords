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

def notify_refund_execute_failed(db: Session, req, child, remark: str) -> None:
    """退款执行失败 → **管理待办**（运营重试入口，WM13 触发点5 / Q5 裁定）。

    P1-3（2026-10-18 第七十五批）：原先不传 `dedup_key`（落默认 `"1"` = 同一退款单**终身一条**）
    → 换号重试（attempt 2）再失败时静默不再提醒。改为按**网关尝试次数**分条
    （同一次尝试重放仍幂等），并 `ignore_handled=True`（处理过之后再失败要重新提醒）。
    """
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.admin_notifications import AdminNotifyService
    from backend.domain.identity.models import Parent

    parent = db.query(Parent).filter(Parent.id == child.parent_id).first() if child else None
    AdminNotifyService(db).send(
        scene=AdminNotification.SCENE_REFUND_EXECUTE_FAILED,
        title="【退款执行失败】",
        content=(
            f"【退款执行失败】{child.name if child else ''}的退款 ￥{req.amount} "
            f"执行失败：{remark}。请处理后重试"
        ),
        ref_type=AdminNotification.REF_REFUND_REQUEST,
        ref_id=str(req.id),
        dedup_key=f"attempt:{req.gateway_attempts or 0}",
        ignore_handled=True,
        applicant_name=f"{parent.name if parent else ''}·{child.name if child else ''}",
        amount=req.amount,
    )
