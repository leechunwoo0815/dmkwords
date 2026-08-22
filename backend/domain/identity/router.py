# backend/domain/identity/router.py — 会员与订单 API
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.common.base_schema import PaginatedResponse
from backend.database import get_db
from backend.domain.identity.schemas import (
    ChildCreateRequest,
    ChildResponse,
    ChildWithParentResponse,
    OrderConfirmRequest,
    OrderCreateRequest,
    OrderResponse,
    ParentCreateRequest,
    ParentResponse,
)
from backend.domain.identity.service import ChildService, OrderService, ParentService
from backend.middleware.admin_rbac import require_perm

router = APIRouter(tags=["identity"])


@router.post("/members/parents", response_model=ParentResponse)
def create_parent(
    body: ParentCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ParentResponse.model_validate(ParentService(db).create(admin, body))


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
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total = OrderService(db).list_orders(page, page_size, status, keyword)
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
