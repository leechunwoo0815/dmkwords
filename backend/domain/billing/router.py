# backend/domain/billing/router.py — 押金 API
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema, PaginatedResponse
from backend.database import get_db
from backend.domain.billing.service import DepositService
from backend.middleware.admin_rbac import require_perm

router = APIRouter(tags=["billing"])


class DepositResponse(BaseSchema):
    id: int
    child_id: int
    child_name: str = ""
    amount: str
    available_amount: str
    deducted_amount: str
    supplemented_total: str
    status: str
    unpaid_balance: str


class DepositLedgerResponse(BaseSchema):
    id: int
    entry_type: str
    amount: str
    balance_after: str
    reason: str
    related_copy_id: int | None
    created_at: datetime = Field(alias="create_time")

    @field_validator("amount", "balance_after", mode="before")
    @classmethod
    def _dec_to_str(cls, v):
        return str(v)


class DeductRequest(BaseSchema):
    amount: str = Field(..., description="赔偿金额（元）")
    reason: str = Field(..., min_length=1, max_length=200, description="事由（关联图书等，留痕）")
    copy_id: int | None = Field(None, description="关联副本ID")


@router.get("/deposits", response_model=PaginatedResponse[DepositResponse])
def list_deposits(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    keyword: str | None = Query(None),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    rows, total = DepositService(db).list_deposits(page, page_size, status, keyword)
    items = []
    for dep, child, _order in rows:
        item = DepositResponse(
            id=dep.id,
            child_id=dep.child_id,
            child_name=child.name,
            amount=str(dep.amount),
            available_amount=str(dep.available_amount),
            deducted_amount=str(dep.deducted_amount),
            supplemented_total=str(dep.supplemented_total),
            status=dep.status,
            unpaid_balance=str(dep.unpaid_balance),
        )
        items.append(item)
    return PaginatedResponse[DepositResponse].create(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get("/deposits/children/{child_id}", response_model=DepositResponse | None)
def get_deposit(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    dep, child_name = DepositService(db).get_child_deposit_summary(child_id)
    if not dep:
        return None
    return DepositResponse(
        id=dep.id,
        child_id=dep.child_id,
        child_name=child_name,
        amount=str(dep.amount),
        available_amount=str(dep.available_amount),
        deducted_amount=str(dep.deducted_amount),
        supplemented_total=str(dep.supplemented_total),
        status=dep.status,
        unpaid_balance=str(dep.unpaid_balance),
    )


@router.get("/deposits/children/{child_id}/ledgers", response_model=list[DepositLedgerResponse])
def get_deposit_ledgers(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    _, ledgers = DepositService(db).get_by_child(child_id)
    out = []
    for entry in ledgers:
        item = DepositLedgerResponse.model_validate(entry)
        item.amount = str(entry.amount)
        item.balance_after = str(entry.balance_after)
        out.append(item)
    return out


@router.post("/deposits/children/{child_id}/orders")
def create_deposit_order(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = DepositService(db).create_deposit_order(admin, child_id)
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "amount": str(order.amount),
        "status": order.status,
    }


@router.post("/deposits/children/{child_id}/supplement-orders")
def create_supplement_order(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    order = DepositService(db).create_supplement_order(admin, child_id)
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "amount": str(order.amount),
        "status": order.status,
    }


@router.post("/deposits/children/{child_id}/deduct", response_model=DepositResponse)
def deduct_deposit(
    child_id: int,
    body: DeductRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    from decimal import Decimal

    dep, child_name = DepositService(db).deduct(
        admin, child_id, Decimal(body.amount), body.reason, body.copy_id
    )
    return DepositResponse(
        id=dep.id,
        child_id=dep.child_id,
        child_name=child_name,
        amount=str(dep.amount),
        available_amount=str(dep.available_amount),
        deducted_amount=str(dep.deducted_amount),
        supplemented_total=str(dep.supplemented_total),
        status=dep.status,
        unpaid_balance=str(dep.unpaid_balance),
    )
