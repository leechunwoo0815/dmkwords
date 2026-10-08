# backend/domain/identity/refund_queries.py — 退款读侧（列表序列化 / 渠道判定展示）
"""为什么单开：`wm10_service.py` 贴着 800 行 god-file 红线，而读侧（列表拼装 + 序列化）
与状态机（apply/review/execute）本来就是两件事——搬出来两边都清爽，也给红线留出余量。

这里的"退款渠道"用的是 `refund_online.resolve_online_source`（与真正执行时**同一个判据**）：
运营在列表上看到的"微信原路 / 线下打款"，与点执行后实际走的路径必然一致。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.identity import refund_online
from backend.domain.identity.models import Child, Order, RefundRequest


def view(r: RefundRequest) -> dict:
    """退款单基础形状（家长端与管理端共用）。"""
    return {
        "id": r.id,
        "kind": r.kind,
        "order_id": r.order_id,
        "child_id": r.child_id,
        "amount": str(r.amount),
        "reason": r.reason,
        "status": r.status,
        "review_remark": r.review_remark,
        "created_at": str(r.created_at),
    }


def my_list(db: Session, child: Child) -> list[dict]:
    """家长视角：该孩子的退款申请列表。"""
    rows = (
        db.query(RefundRequest)
        .filter(RefundRequest.child_id == child.id, RefundRequest.is_deleted == 0)
        .order_by(RefundRequest.id.desc())
        .all()
    )
    return [view(r) for r in rows]


def admin_list(db: Session, status: str | None = None) -> list[dict]:
    """管理端退款台账（最多 200 条）：补齐孩子名/订单信息 + 退款渠道（WM12-B）。"""
    q = db.query(RefundRequest).filter(RefundRequest.is_deleted == 0)
    if status:
        q = q.filter(RefundRequest.status == status)
    rows = q.order_by(RefundRequest.id.desc()).limit(200).all()
    out: list[dict] = []
    for r in rows:
        v = view(r)
        child = db.query(Child).filter(Child.id == r.child_id).first()
        v["child_name"] = child.name if child else f"#{r.child_id}"
        order = None
        if r.order_id:
            order = db.query(Order).filter(Order.id == r.order_id).first()
            if order:
                v["order_no"] = order.order_no
                v["order_type"] = order.order_type
                v["pay_method"] = order.pay_method
        # 执行时会不会走微信原路退回（与 execute 同判据；上面已取的订单传进去复用，不做 N+1）
        online_order, _note = refund_online.resolve_online_source(db, r, order=order)
        v["refund_channel"] = "wechat" if online_order is not None else "offline"
        v["out_refund_no"] = r.out_refund_no or ""
        out.append(v)
    return out
