# backend/domain/billing/miniapp_router.py — 小程序家长端押金 API（WM4-01）
"""押金状态查询 + 补缴订单创建（R-312/R-313：在册/过期可补缴，退会禁）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.domain.billing.service import DepositService
from backend.domain.identity import guards
from backend.domain.identity.models import Child
from backend.domain.reading.miniapp_router import get_current_parent

router = APIRouter(tags=["billing-miniapp"])


def _child_of_parent(db: Session, parent_id: int, child_id: int) -> Child:
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        from backend.common.exceptions import ValidationError

        raise ValidationError("孩子不存在")
    return child


@router.get("/deposits")
def my_deposit(child_id: int, auth: Any = Depends(get_current_parent)):
    """押金账户状态（家长端押金页）。"""
    parent, db = auth
    _child_of_parent(db, parent.id, child_id)
    from decimal import Decimal

    from backend.common.config_service import ConfigService
    from backend.domain.billing.models import Deposit

    standard = Decimal(ConfigService(db).get_value("deposit_amount"))
    dep = db.query(Deposit).filter(Deposit.child_id == child_id, Deposit.is_deleted == 0).first()
    if not dep:
        return {
            "child_id": child_id,
            "status": "unpaid",
            "standard_amount": str(standard),
            "available_amount": "0.00",
            "deducted_amount": "0.00",
            "unpaid_balance": "0.00",
            "need_supplement": False,
            "ledger": [],
        }
    from backend.domain.billing.models import DepositLedger

    ledger_rows = (
        db.query(DepositLedger)
        .filter(DepositLedger.deposit_id == dep.id)
        .order_by(DepositLedger.id.desc())
        .limit(20)
        .all()
    )
    return {
        "child_id": child_id,
        "status": dep.status,
        "standard_amount": str(standard),
        "available_amount": str(dep.available_amount),
        "deducted_amount": str(dep.deducted_amount),
        "unpaid_balance": str(dep.unpaid_balance or Decimal("0")),
        "need_supplement": dep.status
        in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED)
        and dep.available_amount < standard,
        "ledger": [
            {
                "entry_type": r.entry_type,
                "amount": str(r.amount),
                "balance_after": str(r.balance_after),
                "reason": r.reason,
                "created_at": str(r.create_time),
            }
            for r in ledger_rows
        ],
    }


class SupplementRequest(dict):
    pass


@router.post("/deposits/supplement-orders")
def create_supplement_order(body: dict, auth: Any = Depends(get_current_parent)):
    """家长端押金补缴（R-312：差额 = 标准额 − 可用余额；R-313：退会禁）。"""
    parent, db = auth
    child = _child_of_parent(db, parent.id, int(body.get("child_id") or 0))
    guards.require_member_action(db, child, guards.DEPOSIT_SUPPLEMENT)
    import types

    actor = types.SimpleNamespace(id=0, display_name=f"家长(小程序) child={child.id}")
    order = DepositService(db).create_supplement_order(actor, child.id)
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "amount": str(order.amount),
        "status": order.status,
    }
