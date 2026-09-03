# backend/domain/identity/miniapp_router.py — 小程序退款/退会/转让/评估报告（WM10）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.common.base_schema import BaseSchema
from backend.domain.identity.auth import child_of_parent, get_current_parent
from backend.domain.identity.observation_service import ObservationReportService
from backend.domain.identity.transfer_service import TransferService
from backend.domain.identity.wm10_service import RefundService, WithdrawalService

router = APIRouter(tags=["identity-miniapp"])


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
    """家长视角订单列表（A-1/T6 下沉：逻辑在 OrderService.my_orders）。"""
    from backend.domain.identity.order_service import OrderService

    parent, db = auth
    child_of_parent(db, parent.id, child_id)
    return OrderService(db).my_orders(child_id, parent.id)


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
    """家长端消息中心（A-1/T6 下沉：逻辑在 NotificationService.list_mine）。"""
    from backend.common.notifications import NotificationService

    parent, db = auth
    return NotificationService(db).list_mine(parent.id, page, page_size, category)


@router.post("/notifications/read")
def mark_notifications_read(
    body: ReadNotificationsRequest, auth: Any = Depends(get_current_parent)
):
    """标记已读（A-1/T6 下沉：逻辑在 NotificationService.mark_read）。"""
    from backend.common.notifications import NotificationService

    parent, db = auth
    marked = NotificationService(db).mark_read(parent.id, body.ids, body.all)
    return {"ok": True, "marked": marked}


# ---------- 退款 ----------
@router.get("/refund-preview")
def refund_preview(child_id: int, order_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return RefundService(db).preview(child, order_id)


@router.post("/refund-requests")
def refund_apply(body: RefundApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).apply(child, body.order_id, body.reason)
    return {"id": req.id, "status": req.status, "amount": str(req.amount)}


@router.get("/refund-requests")
def refund_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return RefundService(db).my_list(child)


class RefundCancelRequest(BaseSchema):
    child_id: int


@router.post("/refund-requests/{request_id}/cancel")
def refund_cancel(
    request_id: int, body: RefundCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销待审核退款申请（BDD：cancelled、订单恢复；联动撤销退会申请）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    req = RefundService(db).cancel(child, request_id)
    return {"id": req.id, "status": req.status}


# ---------- 退会 ----------
@router.post("/withdrawals")
def withdrawal_apply(body: WithdrawalApplyRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return WithdrawalService(db).apply(child, body.reason)


@router.get("/withdrawals")
def withdrawal_list(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return WithdrawalService(db).my_list(child)


class WithdrawalCancelRequest(BaseSchema):
    child_id: int


@router.post("/withdrawals/{request_id}/cancel")
def withdrawal_cancel(
    request_id: int, body: WithdrawalCancelRequest, auth: Any = Depends(get_current_parent)
):
    """家长撤销进行中的退会申请（applying → cancelled + 解锁）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
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
    child_of_parent(db, parent.id, child_id)
    return ObservationReportService(db).list_for_child(child_id)
