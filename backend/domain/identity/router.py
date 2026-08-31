# backend/domain/identity/router.py — 会员与订单 API
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema, PaginatedResponse
from backend.database import get_db
from backend.domain.identity.observation_service import ObservationReportService
from backend.domain.identity.schemas import (
    ChildCreateRequest,
    ChildResponse,
    ChildUpdateRequest,
    ChildWithParentResponse,
    OrderConfirmRequest,
    OrderCreateRequest,
    OrderResponse,
    ParentCreateRequest,
    ParentResponse,
)
from backend.domain.identity.service import ChildService, OrderService, ParentService
from backend.domain.identity.wm10_service import (
    RefundService,
    TransferService,
    WithdrawalService,
)
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["identity"])


@router.post("/members/parents", response_model=ParentResponse)
def create_parent(
    body: ParentCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ParentResponse.model_validate(ParentService(db).create(admin, body))


@router.get("/members/parents", response_model=list[ParentResponse])
def search_parents(
    keyword: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """家长搜索（W1：建档家长选择器远程搜索，姓名/手机号模糊匹配）。"""
    return [ParentResponse.model_validate(p) for p in ParentService(db).search(keyword)]


@router.get("/members/parents/{parent_id}/children", response_model=list[ChildResponse])
def list_children(
    parent_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return [ChildResponse.model_validate(c) for c in ParentService(db).list_children(parent_id)]


@router.post("/members/parents/{parent_id}/children", response_model=ChildResponse)
def create_child(
    parent_id: int,
    body: ChildCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ChildResponse.model_validate(ChildService(db).create(admin, parent_id, body))


@router.get("/members/children", response_model=PaginatedResponse[ChildWithParentResponse])
def list_children_page(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None),
    status: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total = ChildService(db).list_children(page, page_size, keyword, status)
    items = []
    for child, parent_name, parent_phone in rows:
        item = ChildWithParentResponse.model_validate(child)
        item.parent_name = parent_name
        item.parent_phone = parent_phone
        items.append(item)
    return PaginatedResponse[ChildWithParentResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


class MemberStatusActionRequest(BaseSchema):
    reason: str = Field("", max_length=200, description="操作原因（留痕）")


@router.post("/members/children/{child_id}/mark-pending-evaluation", response_model=ChildResponse)
def mark_pending_evaluation(
    child_id: int,
    body: MemberStatusActionRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """观察期 → 待评估（C13：馆员手动标记；自动转换任务在 WM11）。"""
    return ChildResponse.model_validate(
        ChildService(db).mark_pending_evaluation(admin, child_id, body.reason)
    )


@router.put("/members/children/{child_id}", response_model=ChildResponse)
def update_child(
    child_id: int,
    body: ChildUpdateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """维护孩子资料（C19）：英文名/年级/AR 值（AR 只升不降）。"""
    return ChildResponse.model_validate(
        ChildService(db).update_profile(
            admin, child_id, body.english_name, body.grade, body.ar_level
        )
    )


@router.post("/members/children/{child_id}/evaluate-approve", response_model=OrderResponse)
def evaluate_approve(
    child_id: int,
    body: MemberStatusActionRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """评估通过转正（C13/R-101-5）：创建年费订单（二孩折扣沿用），收款确认后转正式会员。"""
    order = ChildService(db).evaluate_approve(admin, child_id, body.reason)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


@router.post("/orders", response_model=OrderResponse)
def create_order(
    body: OrderCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).create(admin, body)
    return OrderResponse.model_validate(order)


@router.get("/orders", response_model=PaginatedResponse[OrderResponse])
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    order_by: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total = OrderService(db).list_orders(page, page_size, status, keyword, order_by)
    items = []
    for order, child_name, parent_name in rows:
        item = OrderResponse.model_validate(order)
        item.amount = str(order.amount)
        item.child_name = child_name
        item.parent_name = parent_name
        items.append(item)
    return PaginatedResponse[OrderResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/orders/counts")
def order_counts(
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """订单各状态计数（W3 待确认待办视角；语义化键名，WM13 待办聚合预留）。"""
    return OrderService(db).counts()


@router.post("/orders/{order_id}/confirm-payment", response_model=OrderResponse)
def confirm_payment(
    order_id: int,
    body: OrderConfirmRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).confirm_payment(admin, order_id, body)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


@router.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = OrderService(db).cancel(admin, order_id)
    item = OrderResponse.model_validate(order)
    item.amount = str(order.amount)
    return item


class OrderRefundRequest(BaseSchema):
    remark: str = Field("", max_length=200, description="退款说明（留痕）")


@router.post("/orders/{order_id}/refund")
def refund_order(
    order_id: int,
    body: OrderRefundRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """订单退款执行（超管；审核路径的家庭申请流程见 WM10 退款中心）。"""
    order = OrderService(db).refund_order(admin, order_id, body.remark)
    return {"id": order.id, "order_no": order.order_no, "status": order.status}


# ==================== WM10：退款 / 退会 / 转让 / 评估报告（管理端） ====================


class ReviewRequest(BaseSchema):
    approve: bool
    remark: str = Field("", max_length=200, description="审核备注（拒绝必填，家长可见）")


@router.get("/refund-requests")
def admin_refund_list(
    status: str | None = None,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款申请列表（订单类 + 押金类统一，超管逐单审）。"""
    return RefundService(db).admin_list(status)


@router.post("/refund-requests/{request_id}/review")
def admin_refund_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return RefundService(db).review(admin, request_id, body.approve, body.remark)


class RefundExecuteRequest(BaseSchema):
    success: bool = Field(
        ..., description="执行结果：true=退款成功（凭证登记）/ false=失败（可重试）"
    )
    remark: str = Field("", max_length=200, description="退款凭证/失败原因（留痕）")


@router.post("/refund-requests/{request_id}/execute")
def admin_refund_execute(
    request_id: int,
    body: RefundExecuteRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """执行退款（R-308：approved → processing → refunded/failed；线下打款登记凭证）。"""
    return RefundService(db).execute(admin, request_id, body.success, body.remark)


@router.get("/withdrawals")
def admin_withdrawal_list(
    status: str | None = None,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return WithdrawalService(db).admin_list(status)


@router.post("/withdrawals/{request_id}/review")
def admin_withdrawal_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return WithdrawalService(db).review(admin, request_id, body.approve, body.remark)


@router.get("/transfers")
def admin_transfer_list(
    status: str | None = None,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return TransferService(db).admin_list(status)


@router.post("/transfers/{request_id}/review")
def admin_transfer_review(
    request_id: int,
    body: ReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    return TransferService(db).review(admin, request_id, body.approve, body.remark)


@router.post("/children/{child_id}/observation-reports")
async def upload_observation_report(
    child_id: int,
    remark: str = Form(""),
    files: list[UploadFile] = File(...),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """观察期评估报告上传（≤9 张图；家长端可见）。"""
    return ObservationReportService(db).upload_for_admin(admin, child_id, files, remark or None)
