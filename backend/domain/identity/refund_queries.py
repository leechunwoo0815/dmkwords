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


#: 管理端退款台账硬上限（P2-44：上限与"是否已达上限"经响应头如实告知，见 `common/list_meta.py`）
ADMIN_LIST_LIMIT = 200


def admin_list(
    db: Session, status: str | None = None, page: int = 1, page_size: int = ADMIN_LIST_LIMIT
) -> tuple[list[dict], int]:
    """管理端退款台账（**服务端分页**）：补齐孩子名/订单信息 + 退款渠道（WM12-B）。

    P1-7（2026-10-20 第八轮）：原先"只给最近 200 条"——积压超 200 条后**最老的 pending 被挤出
    窗口**，而侧边栏徽标仍显示待审 N（"徽标说有、页面看不到"，资金待办被藏）。现在按页取 +
    返回**当前筛选下的总数**（路由写进 `X-Total-Count`），前端按服务端分页翻得动。
    `page_size` 上限仍锁 `ADMIN_LIST_LIMIT`（单页最多 200，防一次拉爆）。

    WM12-C（审查 P2-8）：孩子 / 订单 / 押金候选三条 `IN` 批量预载，每请求 SQL **与行数无关**。
    返回 `(rows, total)`——`total` 与列表**同一个查询对象**算出来（口径必然一致）。
    """
    q = db.query(RefundRequest).filter(RefundRequest.is_deleted == 0)
    if status:
        q = q.filter(RefundRequest.status == status)
    total = q.count()
    size = max(1, min(int(page_size), ADMIN_LIST_LIMIT))
    offset = max(0, (max(1, int(page)) - 1) * size)
    rows = q.order_by(RefundRequest.id.desc()).offset(offset).limit(size).all()
    if not rows:
        return [], total
    child_ids = {r.child_id for r in rows}
    order_ids = {r.order_id for r in rows if r.order_id}
    deposit_child_ids = {
        r.child_id for r in rows if r.kind == RefundRequest.KIND_DEPOSIT and r.deposit_id
    }
    children = {c.id: c for c in db.query(Child).filter(Child.id.in_(child_ids)).all()}
    orders = (
        {o.id: o for o in db.query(Order).filter(Order.id.in_(order_ids)).all()}
        if order_ids
        else {}
    )
    deposits = refund_online.deposit_paid_orders(db, list(deposit_child_ids))
    out: list[dict] = []
    for r in rows:
        v = view(r)
        child = children.get(r.child_id)
        v["child_name"] = child.name if child else f"#{r.child_id}"
        order = orders.get(r.order_id) if r.order_id else None
        if order:
            v["order_no"] = order.order_no
            v["order_type"] = order.order_type
            v["pay_method"] = order.pay_method
        # 执行时会不会走微信原路退回（与 execute 同判据；预载对象传进去复用，不做 N+1）
        online_order, _note = refund_online.resolve_online_source(
            db, r, order=order, deposit_orders=deposits.get(r.child_id, [])
        )
        v["refund_channel"] = "wechat" if online_order is not None else "offline"
        v["out_refund_no"] = r.out_refund_no or ""
        v["gateway_unknown_at"] = str(r.gateway_unknown_at or "")
        out.append(v)
    return out, total


def refund_center_counts(db: Session) -> dict:
    """退款中心三个台账的**按状态计数**（P1-7，2026-10-20 第八轮）。

    为什么必须有它：三个列表改成服务端分页后，前端只拿得到"本页那几行"——用本页行数算
    徽标就会出现「侧边栏说有待审 N、页面显示 0」（本轮 P2-45 同族的"假 0"）。
    计数口径与列表**同表同过滤**（`is_deleted = 0`），一次 `GROUP BY status` 拿全。

    返回 `{"refund": {status: n}, "withdrawal": {...}, "transfer": {...}}`。
    """
    from sqlalchemy import func

    from backend.domain.identity.models import TransferRequest, WithdrawalRequest

    out: dict[str, dict[str, int]] = {}
    for key, model in (
        ("refund", RefundRequest),
        ("withdrawal", WithdrawalRequest),
        ("transfer", TransferRequest),
    ):
        rows = (
            db.query(model.status, func.count(model.id))
            .filter(model.is_deleted == 0)
            .group_by(model.status)
            .all()
        )
        out[key] = {str(s): int(c) for s, c in rows}
    return out
