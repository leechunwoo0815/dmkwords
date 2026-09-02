# backend/domain/identity/miniapp_router.py — 小程序退款/退会/转让/评估报告（WM10）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.domain.identity.models import Child
from backend.domain.identity.observation_service import ObservationReportService
from backend.domain.identity.transfer_service import TransferService
from backend.domain.identity.wm10_service import RefundService, WithdrawalService
from backend.domain.reading.miniapp_router import get_current_parent

router = APIRouter(tags=["identity-miniapp"])


def _child_of_parent(db: Session, parent_id: int, child_id: int) -> Child:
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


class RefundApplyRequest(BaseSchema):
    child_id: int
    order_id: int
    reason: str


class WithdrawalApplyRequest(BaseSchema):
    child_id: int
    reason: str


class TransferApplyRequest(BaseSchema):
    source_child_id: int
    target_child_id: int


# ---------- 订单（家长视角，退款申请用） ----------
@router.get("/orders")
def my_orders(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    _child_of_parent(db, parent.id, child_id)
    from sqlalchemy import or_

    from backend.domain.identity.models import Order

    # X7：or_ 查询——孩子名下单 ∪ 家长级单（child_id NULL；schema 注释
    # 「活动费可能家长级」，漏查会丢 99 元首场活动费等家长级记录）
    rows = (
        db.query(Order)
        .filter(
            or_(
                Order.child_id == child_id,
                (Order.child_id.is_(None)) & (Order.parent_id == parent.id),
            ),
            Order.is_deleted == 0,
        )
        .order_by(Order.id.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "order_no": r.order_no,
            "order_type": r.order_type,
            "amount": str(r.amount),
            "status": r.status,
            "refund_status": r.refund_status or "",
            "created_at": str(r.create_time),
            "paid_at": str(r.paid_at) if r.paid_at else None,
        }
        for r in rows
    ]


# ---------- WM11 消息中心（家长端站内消息） ----------


class ReadNotificationsRequest(BaseSchema):
    ids: list[int] = []
    all: bool = False


@router.get("/notifications")
def my_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    auth: Any = Depends(get_current_parent),
):
    parent, db = auth
    from backend.common.notification_models import Notification

    unread = (
        db.query(func.count(Notification.id))
        .filter(
            Notification.parent_id == parent.id,
            Notification.is_deleted == 0,
            Notification.read_at.is_(None),
        )
        .scalar()
        or 0
    )
    q = db.query(Notification).filter(
        Notification.parent_id == parent.id, Notification.is_deleted == 0
    )
    if category:
        q = q.filter(Notification.category == category)
    total = q.count()
    rows = q.order_by(Notification.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "unread": unread,
        "total": total,
        "items": [
            {
                "id": n.id,
                "category": n.category,
                "scene": n.scene,
                "title": n.title,
                "content": n.content,
                "read": n.is_read,
                "created_at": n.create_time.strftime("%Y-%m-%d %H:%M") if n.create_time else "",
            }
            for n in rows
        ],
    }


@router.post("/notifications/read")
def mark_notifications_read(
    body: ReadNotificationsRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    from datetime import datetime

    from backend.common.notification_models import Notification

    if body.all:
        rows = (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent.id,
                Notification.is_deleted == 0,
                Notification.read_at.is_(None),
            )
            .all()
        )
    else:
        rows = (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent.id,
                Notification.id.in_(body.ids),
                Notification.is_deleted == 0,
            )
            .all()
        )
    for n in rows:
        if n.read_at is None:
            n.read_at = datetime.now()
    db.commit()
    return {"ok": True, "marked": len(rows)}


# ---------- 退款 ----------
@router.get("/refund-preview")
def refund_preview(child_id: int, order_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return RefundService(db).preview(child, order_id)


@router.post("/refund-requests")
def refund_apply(body: RefundApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).apply(child, body.order_id, body.reason)
    return {"id": req.id, "status": req.status, "amount": str(req.amount)}


@router.get("/refund-requests")
def refund_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return RefundService(db).my_list(child)


class RefundCancelRequest(BaseSchema):
    child_id: int


@router.post("/refund-requests/{request_id}/cancel")
def refund_cancel(
    request_id: int, body: RefundCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销待审核退款申请（BDD：cancelled、订单恢复；联动撤销退会申请）。"""
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).cancel(child, request_id)
    return {"id": req.id, "status": req.status}


# ---------- 退会 ----------
@router.post("/withdrawals")
def withdrawal_apply(body: WithdrawalApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    return WithdrawalService(db).apply(child, body.reason)


@router.get("/withdrawals")
def withdrawal_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return WithdrawalService(db).my_list(child)


class WithdrawalCancelRequest(BaseSchema):
    child_id: int


@router.post("/withdrawals/{request_id}/cancel")
def withdrawal_cancel(
    request_id: int, body: WithdrawalCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销进行中的退会申请（applying → cancelled + 解锁）。"""
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    req = WithdrawalService(db).cancel(child, request_id)
    return {"id": req.id, "status": req.status}


# ---------- 权益转让 ----------
@router.post("/transfers")
def transfer_apply(body: TransferApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).apply(parent, body.source_child_id, body.target_child_id)


@router.get("/transfers")
def transfer_list(auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).my_list(parent)


@router.get("/transfers/conditions")
def transfer_conditions(
    source_child_id: int, target_child_id: int, auth: Any = Depends(get_current_parent)
):
    """转让前置条件核对（前端逐条展示差什么）。"""
    parent, db = auth
    svc = TransferService(db)
    source = svc._child(source_child_id)
    target = svc._child(target_child_id)
    return {"conditions": svc.check_conditions(parent, source, target)}


@router.post("/transfers/{transfer_id}/cancel")
def transfer_cancel(transfer_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    return TransferService(db).cancel(parent, transfer_id)


# ---------- 评估报告 ----------
@router.get("/observation-reports")
def observation_reports(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    _child_of_parent(db, parent.id, child_id)
    return ObservationReportService(db).list_for_child(child_id)
