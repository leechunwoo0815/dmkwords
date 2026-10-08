# backend/domain/identity/refund_online.py — 线上原路退款编排（WM12-B）
"""为什么单独一个模块：`wm10_service.py` 已经贴着 800 行 god-file 红线（宪法架构关硬门禁），
而"原路退款"这块逻辑（渠道判定 + 三段式调网关 + 回调终态）自成一体、与人工登记路径正交，
抽出来两边都看得清（同 `refund_rules.py` 的先例）。

三个函数都不依赖 `RefundService` 类：落终态那一步用 **回调注入**（`finalize=`），
免得模块之间循环 import。渠道判定与单号反解的**唯一事实源**都在这里。

口径（docs/09 WM12-B §二）：
1. 渠道跟着**原单**走（`pay_method=wechat` 且有 `transaction_id`），不看谁发起退款；
2. 网关"受理成功"≠ 退款成功——`PROCESSING` 必须留在"执行中"等回调；
3. `out_refund_no` 是微信侧幂等键，格式 `RF{退款单id}-{第几次}-{第几笔}`，回调靠它反解。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from backend.common.exceptions import NotFoundError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Order, RefundRequest

logger = logging.getLogger(__name__)


def find_by_out_refund_no(db: Session, out_refund_no: str) -> RefundRequest | None:
    """按商户退款单号反解退款单（`RF{id}-...` → id）。

    为什么靠反解而不是新表：回调只带回这个字符串，而它本身就含 id——多一张映射表
    就多一处可能与事实不一致的地方。
    """
    token = (out_refund_no or "").strip()
    if not token.startswith("RF"):
        return None
    head = token[2:].split("-", 1)[0]
    if not head.isdigit():
        return None
    return (
        db.query(RefundRequest)
        .filter(RefundRequest.id == int(head), RefundRequest.is_deleted == 0)
        .first()
    )


def deposit_paid_orders(db: Session, child_ids: list[int]) -> dict[int, list[Order]]:
    """孩子的**线上押金支付候选**（按孩子分组）——原路退款判据与列表页共用同一查询。

    单开出来是为了批量预载（审查 P2-8：列表页逐行查会 N+1）。
    """
    if not child_ids:
        return {}
    rows = (
        db.query(Order)
        .filter(
            Order.child_id.in_(child_ids),
            Order.is_deleted == 0,
            Order.status == Order.STATUS_PAID,
            Order.order_type.in_([Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT]),
            Order.pay_method == "wechat",
            Order.transaction_id.isnot(None),
            Order.transaction_id != "",
        )
        .all()
    )
    grouped: dict[int, list[Order]] = {}
    for o in rows:
        grouped.setdefault(o.child_id, []).append(o)
    return grouped


def resolve_online_source(
    db: Session,
    req: RefundRequest,
    *,
    order: Order | None = None,
    deposit_orders: list[Order] | None = None,
) -> tuple[Order | None, str]:
    """找出这笔退款对应的**线上支付原单**。返回 (原单, 说明)。

    找不到时返回 `(None, 说明)` → 走线下打款登记，说明会进审计（为什么没自动原路退）。
    `order` / `deposit_orders` 可由调用方预载（列表页逐行判定渠道时复用，避免 N+1，审查 P2-8）。
    押金的特殊口径：一笔微信退款只能对应一笔原交易，所以**只有该孩子的押金由单笔线上支付
    构成**时才自动原路退；多笔（首缴+补缴）回落线下并留痕，绝不拼单——拼错了钱退到别人账上。
    """
    note = ""
    if req.kind == RefundRequest.KIND_ORDER and req.order_id:
        if order is None:
            order = db.query(Order).filter(Order.id == req.order_id).first()
    elif req.kind == RefundRequest.KIND_DEPOSIT and req.deposit_id:
        order = None
        if deposit_orders is None:
            deposit_orders = deposit_paid_orders(db, [req.child_id]).get(req.child_id, [])
        if len(deposit_orders) == 1:
            order = deposit_orders[0]
        elif len(deposit_orders) > 1:
            note = (
                f"押金由 {len(deposit_orders)} 笔线上支付构成，未自动原路退（请线下打款或分笔退）"
            )
        else:
            note = "押金无线上支付记录，按线下打款登记"
    if order is None:
        return None, note or "原单非线上支付，按线下打款登记"
    # 判据只看"当初钱是怎么进来的"（pay_method + 支付单号）——**不看订单当前状态**：
    # 退款渠道是历史事实，退款完成后列表仍应显示"微信原路"（曾把 state==REFUNDED 也算进来，
    # 结果退完款渠道就变回"线下打款"，运营看到的与事实不符；防重复退款由退款单状态机把关）
    if order.pay_method != "wechat":
        return None, "原单非线上支付，按线下打款登记"
    if not (order.transaction_id or "").strip():
        return None, "原单缺微信支付单号，按线下打款登记"
    return order, ""


def route_online(
    db: Session,
    admin: Any,
    req: RefundRequest,
    *,
    remark: str,
    manual_override: bool,
    finalize: Callable[..., None],
) -> tuple[bool, str]:
    """退款执行的分流口：原单是线上支付 → 走三段式原路退款并返回 `(True, "")`；
    否则返回 `(False, 说明)` 让调用方走线下打款登记（说明进审计）。"""
    order, note = resolve_online_source(db, req)
    if order is None:
        return False, note
    submit_gateway_refund(
        db,
        admin,
        req,
        order,
        remark=remark,
        manual_override=manual_override,
        finalize=finalize,
    )
    return True, ""


def submit_gateway_refund(
    db: Session,
    admin: Any,
    req: RefundRequest,
    order: Order,
    *,
    remark: str,
    manual_override: bool,
    finalize: Callable[..., None],
) -> None:
    """线上原路退款：三段式（模式手册 P2）。

    Phase 1 单号与状态落库 + 提交（放掉行锁）→ Phase 2 事务外调网关（异常不回滚 Phase 1：
    网络超时窗口内对方可能已受理，置回可重试态会丢回调匹配，善后交给对账与人工）
    → Phase 3 锁内复核：回调已置终态就认回调，否则按网关返回落终态或留在"执行中"。
    """
    from backend.common.async_utils import run_coro
    from backend.common.gateways.payment.types import PaymentRefundRequest, yuan_to_cents
    from backend.config import get_settings
    from backend.integrations.payment import get_payment_gateway

    # Phase 1
    req.gateway_attempts = (req.gateway_attempts or 0) + 1
    out_refund_no = f"RF{req.id}-{req.gateway_attempts}-1"
    req.out_refund_no = out_refund_no
    db.commit()

    # Phase 2
    settings = get_settings()
    gateway = get_payment_gateway()
    resp = None
    error_message = ""
    gateway_error = False
    try:
        resp = run_coro(
            gateway.refund(
                PaymentRefundRequest(
                    out_trade_no=order.order_no,
                    refund_amount=Decimal(yuan_to_cents(req.amount)),
                    total_amount=Decimal(yuan_to_cents(order.amount)),
                    reason=(remark or "用户申请退款")[:80],
                    out_refund_no=out_refund_no,
                    notify_url=settings.WECHAT_REFUND_NOTIFY_URL,
                )
            )
        )
        error_message = resp.error_message
    except Exception as exc:  # 网关炸了 = **结果未知**（不是失败）：见 Phase 3 的未知态处理
        logger.error("微信退款调用异常 req=%s: %s", req.id, exc, exc_info=True)
        error_message = f"网关异常：{exc}"
        gateway_error = True

    # Phase 3
    fresh = (
        db.query(RefundRequest)
        .filter(RefundRequest.id == req.id, RefundRequest.is_deleted == 0)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if fresh is None or fresh.status == RefundRequest.STATUS_REFUNDED:
        return  # 回调赢了（或单被删）：认回调结果，不再改
    if gateway_error:
        # WM12-C（审查 P0-1）：超时/异常的窗口里微信**可能已受理**——留在"执行中" + 打未知态标记。
        # 绝不落 failed：failed 可重提且会换新的 out_refund_no，上一笔若已受理就是二次出款，
        # 对账也查不出来（本项修复前正是如此）。收口靠退款回调或人工查单（resolve_unknown）。
        fresh.gateway_unknown_at = datetime.now()
        publish_audit(
            db,
            admin=admin,
            action="refund.gateway_unknown",
            target_type="refund_request",
            target_id=str(fresh.id),
            detail={
                "out_refund_no": out_refund_no,
                "amount": str(fresh.amount),
                "error": error_message[:200],
            },
            reason="网关调用异常，结果未知：先查单确认微信侧结果，再决定是否重试",
        )
        db.commit()
        return
    # 走到这里说明网关**明确应答**了（受理或拒绝）——结果不再未知，清掉未知态标记
    # （无论后面落"已退款"、"执行中"还是"失败"，都不是"不知道对方收没收到"）
    fresh.gateway_unknown_at = None
    if resp is not None and resp.success:
        fresh.gateway_refund_id = resp.refund_id or fresh.gateway_refund_id
        state = (resp.state or "SUCCESS").upper()
        if state == "SUCCESS":
            finalize(
                admin,
                fresh,
                success=True,
                remark=f"{remark}（微信原路退款 {out_refund_no}）".strip(),
                manual_override=manual_override,
                channel="wechat",
            )
            return
        if state in ("CLOSED", "ABNORMAL"):
            finalize(
                admin,
                fresh,
                success=False,
                remark=f"微信退款被关闭/异常（{state}，{out_refund_no}）",
                manual_override=manual_override,
                channel="wechat",
            )
            return
        # PROCESSING（或网关未返回状态）：微信已受理但钱还在路上 → 留在"执行中"等退款结果通知
        publish_audit(
            db,
            admin=admin,
            action="refund.gateway_submitted",
            target_type="refund_request",
            target_id=str(fresh.id),
            detail={
                "out_refund_no": out_refund_no,
                "gateway_refund_id": fresh.gateway_refund_id or "",
                "state": state or "UNKNOWN",
                "amount": str(fresh.amount),
            },
            reason="微信原路退款已受理，等待退款结果通知",
        )
        # 先发布后提交（审计与状态同一事务）：早先 commit 在前、审计在后，请求结束 close()
        # 把未提交的审计回滚掉——"留痕"其实没留（WM12-C 顺带修正）
        db.commit()
        return
    finalize(
        admin,
        fresh,
        success=False,
        remark=f"微信退款失败：{error_message or '未知原因'}（{out_refund_no}）"[:200],
        manual_override=manual_override,
        channel="wechat",
    )


def resolve_unknown(
    db: Session,
    admin: Any,
    request_id: int,
    *,
    finalize: Callable[..., None],
) -> dict:
    """退款**未知态**收口（WM12-C 审查 P0-1）：调微信查单确认上一笔到底受理没有。

    这是未知态唯一的出路（回调之外）：`execute` 在 `processing` 上被状态机拦住，
    而"换新单号重提"在未知态下是绝对禁止的（可能二次出款）。

    分支（查单结论 → 动作）：
    - 查无此单（未受理）→ 清未知态 + 落 `failed`（**现在**重试是安全的，会换新 `out_refund_no`）；
    - `SUCCESS` → 直接落"已退款"（回调可能没到/迟到）；
    - `PROCESSING` → 清未知态，留在"执行中"等退款回调；
    - `CLOSED`/`ABNORMAL` → 钱没退出去 → 清未知态 + 落 `failed`（可重提）；
    - 查单本身失败 → **未知态保持、状态不动**（宁可让人再查一次，也不拿猜测去重试）。
    """
    from backend.common.async_utils import run_coro
    from backend.common.exceptions import ValidationError
    from backend.integrations.payment import get_payment_gateway

    req = (
        db.query(RefundRequest)
        .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not req:
        raise NotFoundError("退款申请不存在")
    if not (req.out_refund_no or "").strip():
        raise ValidationError("该退款单没有商户退款单号（没走过网关），无需查单")
    if req.gateway_unknown_at is None and req.status != RefundRequest.STATUS_PROCESSING:
        raise ValidationError("该退款单不处于待查状态（只有执行中/未知态需要查单）")

    out_refund_no = req.out_refund_no
    try:
        result = run_coro(get_payment_gateway().query_refund(out_refund_no))
    except Exception as exc:
        logger.error("退款查单失败 req=%s: %s", req.id, exc, exc_info=True)
        publish_audit(
            db,
            admin=admin,
            action="refund.gateway_query_failed",
            target_type="refund_request",
            target_id=str(req.id),
            detail={"out_refund_no": out_refund_no, "error": str(exc)[:200]},
            reason="退款查单失败：微信侧结果仍未确认，未知态保持",
        )
        db.commit()
        return {
            "id": req.id,
            "status": req.status,
            "gateway_state": "UNKNOWN",
            "resolved": False,
            "note": "查单失败，微信侧结果仍未确认（未知态保持，请稍后再查）",
        }

    state = (result.state or "").upper()
    if not result.found:
        # 未受理 = 可安全重试：清未知态 + 落 failed（这条路径上"重试"不再有二次出款的可能是查单换来的）
        req.gateway_unknown_at = None
        finalize(
            admin,
            req,
            success=False,
            remark=f"微信查单确认未受理（{out_refund_no}），可安全重试",
            manual_override=False,
            channel="wechat",
            channel_note="退款查单：微信侧查无此单，已确认未受理",
        )
        return {
            "id": req.id,
            "status": req.status,
            "gateway_state": "NOT_FOUND",
            "resolved": True,
            "note": "微信侧查无此单（未受理）→ 已置失败，可重新执行（会生成新的商户退款单号）",
        }
    if state == "SUCCESS":
        req.gateway_unknown_at = None
        if result.refund_id:
            req.gateway_refund_id = result.refund_id
        finalize(
            admin,
            req,
            success=True,
            remark=f"微信查单确认已退款（{out_refund_no}）",
            manual_override=False,
            channel="wechat",
            channel_note="退款查单：微信侧已退款成功",
        )
        return {
            "id": req.id,
            "status": req.status,
            "gateway_state": state,
            "resolved": True,
            "note": "微信侧已退款成功 → 已落已退款（回调可能迟到）",
        }
    if state in ("CLOSED", "ABNORMAL"):
        req.gateway_unknown_at = None
        finalize(
            admin,
            req,
            success=False,
            remark=f"微信查单结论：退款被关闭/异常（{state}，{out_refund_no}）",
            manual_override=False,
            channel="wechat",
            channel_note=f"退款查单：{state}",
        )
        return {
            "id": req.id,
            "status": req.status,
            "gateway_state": state,
            "resolved": True,
            "note": f"微信侧退款 {state}（未出款）→ 已置失败，可重新执行",
        }
    # PROCESSING / 其它中间态：受理了但钱在路上——未知态解除，继续等回调
    req.gateway_unknown_at = None
    publish_audit(
        db,
        admin=admin,
        action="refund.gateway_queried",
        target_type="refund_request",
        target_id=str(req.id),
        detail={"out_refund_no": out_refund_no, "state": state or "UNKNOWN"},
        reason="退款查单：微信侧仍在处理中，未知态解除，等退款回调",
    )
    db.commit()
    return {
        "id": req.id,
        "status": req.status,
        "gateway_state": state or "UNKNOWN",
        "resolved": True,
        "note": "微信侧处理中（已受理）→ 未知态解除，留在执行中等回调",
    }


def finalize_gateway_result(
    db: Session,
    request_id: int,
    *,
    success: bool,
    remark: str,
    finalize: Callable[..., None],
) -> dict:
    """微信退款结果通知的落库入口：把"执行中"推到终态。

    **幂等与乱序守卫**（模式手册 P3 / F-031）：已 `refunded` 的单，重复通知或迟到的失败通知
    一律直接返回——钱已经退成功，不能被后来的旧通知改回失败。
    """
    import types

    req = (
        db.query(RefundRequest)
        .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not req:
        raise NotFoundError("退款申请不存在")
    actor = types.SimpleNamespace(id=0, display_name="微信退款回调")
    if req.status == RefundRequest.STATUS_REFUNDED:
        return {"id": req.id, "status": req.status, "ignored": True}
    if req.status != RefundRequest.STATUS_PROCESSING:
        # WM12-C（审查 P2-14）：状态不可推进（如 pending/rejected/cancelled）→ **忽略并留痕**，
        # 不再 `assert_transition` 抛错：抛错会让 Router 返回 500 → 微信按规则重发 24 小时，
        # 而本地状态问题重试不会自愈（每次重发都失败，纯噪声+占资源）。
        publish_audit(
            db,
            admin=actor,
            action="refund.notify_state_skipped",
            target_type="refund_request",
            target_id=str(req.id),
            detail={"status": req.status, "remark": remark[:120]},
            reason="退款通知到达但退款单状态不可推进，已忽略（需人工核对）",
        )
        db.commit()
        return {
            "id": req.id,
            "status": req.status,
            "ignored": True,
            "reason": "state_not_processable",
        }
    req.gateway_unknown_at = None  # 回调即明确结论：未知态解除
    finalize(actor, req, success=success, remark=remark, manual_override=False, channel="wechat")
    return {"id": req.id, "status": req.status}
