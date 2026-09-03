# backend/domain/billing/miniapp_router.py — 小程序家长端押金 API（WM4-01）
"""押金状态查询 + 补缴订单创建（R-312/R-313：在册/过期可补缴，退会禁）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.domain.billing.service import DepositService
from backend.domain.identity import guards
from backend.domain.identity.auth import child_of_parent, get_current_parent

router = APIRouter(tags=["billing-miniapp"])


@router.get("/deposits")
def my_deposit(child_id: int, auth: Any = Depends(get_current_parent)):
    """押金账户状态（家长端押金页，A-1/T6 下沉：逻辑在 DepositService.my_deposit_view）。"""
    parent, db = auth
    child_of_parent(db, parent.id, child_id)
    return DepositService(db).my_deposit_view(child_id)


class SupplementRequest(dict):
    pass


@router.post("/deposits/supplement-orders")
def create_supplement_order(body: dict, auth: Any = Depends(get_current_parent)):
    """家长端押金补缴（R-312：差额 = 标准额 − 可用余额；R-313：退会禁）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, int(body.get("child_id") or 0))
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
