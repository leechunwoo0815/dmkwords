# backend/domain/identity/deposit_refund_guards.py — 押金退款的余额闸（2026-10-09 资金 P0-D）
"""**押金是担保账户，不是普通订单**：退款绝不允许把 `available_amount` 打成负数。

为什么单独成模块（两重原因，与 `refund_rules.py` 同一处置）：
① 这两处规则（执行前复核 / 终态超额留痕）语义自洽，与 `wm10_service` 的流程编排无关；
② `wm10_service.py` 触发架构关"god file 禁止"（>800 行）——服务类只留编排。

口径（不可回退，缺陷背景见 docs/09 第三十八轮）：
  执行前 —— 可用余额 < 本次申请金额 → **拒执行**（422），状态不流转（人工处理后仍可执行）；
           复核必须在**提交网关之前**：线上单一旦提交，钱就出去了，那时再发现晚了。
  终态   —— 真出现超额（旁路创建/历史脏数据）→ **不掩盖**：台账照实记负（损失的真实表示），
           另发一条异常审计 `deposit.refund_overdraft` 让对账与人工能逮到。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.exceptions import ValidationError


def lock_deposit(db: Session, deposit_id: int):
    """锁押金行读余额：复核与扣减必须同一把锁，否则"两个执行各自读到 1200"照样穿透。"""
    from backend.domain.billing.models import Deposit

    return (
        db.query(Deposit)
        .filter(Deposit.id == deposit_id)
        .with_for_update()
        .populate_existing()
        .first()
    )


def assert_deposit_refund_executable(db: Session, req) -> None:
    """押金退款执行前余额复核（调用点：`RefundService.execute`，提交网关之前）。"""
    dep = lock_deposit(db, req.deposit_id)
    if dep and dep.available_amount < req.amount:
        raise ValidationError(
            f"押金可用余额不足（可用 {dep.available_amount}，本次申请 {req.amount}）——"
            "疑似重复退款单，请人工核对后再执行"
        )


def publish_deposit_overdraft(db: Session, *, admin, req, dep) -> None:
    """超额执行留痕（台账照实记负 + 异常审计）——正常路径已被执行前复核拦住。"""
    from backend.domain.catalog.audit_events import publish_audit

    publish_audit(
        db,
        admin=admin,
        action="deposit.refund_overdraft",
        target_type="deposit",
        target_id=str(dep.id),
        detail={
            "available_before": str(dep.available_amount),
            "refund_amount": str(req.amount),
            "refund_request_id": req.id,
        },
        reason="押金退款超额（可用余额不足仍执行）——疑似重复退款单，请人工核对",
    )
