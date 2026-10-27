# backend/domain/identity/order_views.py — 订单列表的批量视图辅助
"""订单列表的批量查询（P2-4 名称映射 + docs/23 §四 押金退款展示）。

放独立模块而非 `order_service.py` 内：该文件已逼近 800 行 god-file 关
（加两个辅助会破线）。两者都是纯读、无事务副作用。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.domain.identity.models import Child, Order, Parent


def name_maps(db: Session, orders: list[Order]) -> tuple[dict[int, str], dict[int, str]]:
    """订单行的孩子/家长名**批查**（P2-4，2026-10-11）——每行 2 条 SELECT → 总计 2 条。"""
    cids = {o.child_id for o in orders if o.child_id}
    pids = {o.parent_id for o in orders if o.parent_id}
    cmap = dict(db.query(Child.id, Child.name).filter(Child.id.in_(cids)).all()) if cids else {}
    pmap = dict(db.query(Parent.id, Parent.name).filter(Parent.id.in_(pids)).all()) if pids else {}
    return cmap, pmap


def deposit_refund_map(db: Session, child_ids: list[int | None]) -> dict[int, dict]:
    """押金退款展示（docs/23 §四）：按孩子汇总押金台账的累计退款额 + 押金状态。

    订单状态保持 paid（支付事实不改）；退款事实以押金台账 `ENTRY_REFUND` 为准，
    部分退款也能如实表达。批量查询（两查），不随订单行数增长。
    """
    from backend.domain.billing.models import Deposit, DepositLedger

    ids = sorted({i for i in child_ids if i is not None})
    if not ids:
        return {}
    deposits = (
        db.query(Deposit.id, Deposit.child_id, Deposit.status)
        .filter(Deposit.child_id.in_(ids), Deposit.is_deleted == 0)
        .all()
    )
    if not deposits:
        return {}
    sums = dict(
        db.query(
            DepositLedger.deposit_id,
            func.coalesce(func.sum(DepositLedger.amount), 0),
        )
        .filter(
            DepositLedger.deposit_id.in_([d.id for d in deposits]),
            DepositLedger.entry_type == DepositLedger.ENTRY_REFUND,
            DepositLedger.is_deleted == 0,
        )
        .group_by(DepositLedger.deposit_id)
        .all()
    )
    out: dict[int, dict] = {}
    for d in deposits:
        amount = Decimal(str(sums.get(d.id, 0))).quantize(Decimal("0.01"))
        out[d.child_id] = {
            "deposit_status": d.status,
            "deposit_refunded_amount": f"{amount:.2f}",
        }
    return out
