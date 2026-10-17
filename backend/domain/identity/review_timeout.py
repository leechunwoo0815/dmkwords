# backend/domain/identity/review_timeout.py — 审核超时提醒（退款 / 退会）
"""P2-28（2026-10-17 第六十九批·第四轮大审查）。

背景（报告 §三·28）：配置中心 `refund_review_timeout_hours` / `withdrawal_review_timeout_days`
两个键**全仓零读取方**，页面却宣称"修改立即生效"；`docs/03` FEAT-068 承诺的"审核超时提醒"
实际只有转让一条链（`transfer_expiring_warn`）。退款/退会申请可以无限期躺在待审，
唯一"兜底"是馆员自己记得去翻收件箱。

口径（三条，与转让预警同款，**先落在本文档再落代码**）：
1. **只提醒、不自动取消**：`withdrawal_review_timeout_days` 的种子描述原先写着"自动取消"，
   但"系统自动取消家长的退会申请"属产品决策（已挂拍板清单）——实现按"提醒"落，描述同步改。
2. **幂等 = 每单一条**：唯一索引是 `(scene, ref_type, ref_id, dedup_key, is_deleted)`，
   dedup 取默认 `"1"` → 同一张申请单只提醒一次，不随每次扫描刷屏（红线 156：
   去重键必须是"不会重来的事实"——这里的"事实"就是申请单本身）。
3. **显示态由收件箱 resolver 实时算**：新场景复用既有 `ref_type`（`refund_request` /
   `withdrawal_request`）→ 单据审结/撤销后提醒**自动「已审结/已失效」**、计数自动归零，
   永远不会变成孤儿（S1 口径，与 `transfer_expiring_warn` 一致）。

为什么单独一个模块（而不是塞进 `wm10_service.py`）：两个提醒分属退款/退会两个服务，
且 `wm10_service.py` 已贴近 god-file 800 行上限——同类操作用例的抽法见
`identity/order_pricing.py`（定价）与 `catalog/damage_ops.py`（报损待办）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.common.config_service import ConfigService
from backend.domain.identity.models import Child, Parent, RefundRequest, WithdrawalRequest

#: 配置默认值（与 `seeds/seed_configs.py` 的种子值同源；配置缺失/非法时兜底）
DEFAULT_REFUND_TIMEOUT_HOURS = 72
DEFAULT_WITHDRAWAL_TIMEOUT_DAYS = 3


def _applicant_name(db: Session, child_id: int) -> str:
    child = db.query(Child).filter(Child.id == child_id).first()
    parent = db.query(Parent).filter(Parent.id == child.parent_id).first() if child else None
    return f"{parent.name if parent else ''}·{child.name if child else ''}"


def refund_review_timeout_warn(db: Session) -> int:
    """退款申请待审超过 `refund_review_timeout_hours` 小时 → 逐单一条管理待办提醒。"""
    hours = max(
        1, ConfigService(db).get_int("refund_review_timeout_hours", DEFAULT_REFUND_TIMEOUT_HOURS)
    )
    deadline = datetime.now() - timedelta(hours=hours)
    rows = (
        db.query(RefundRequest)
        .filter(
            RefundRequest.status == RefundRequest.STATUS_PENDING,
            RefundRequest.created_at <= deadline,
            RefundRequest.is_deleted == 0,
        )
        .all()
    )
    svc = AdminNotifyService(db)
    sent = 0
    for r in rows:
        applicant = _applicant_name(db, r.child_id)
        kind_label = "押金退款" if r.kind == RefundRequest.KIND_DEPOSIT else "订单退款"
        ok = svc.send(
            scene=AdminNotification.SCENE_REFUND_REVIEW_TIMEOUT,
            title="【退款审核超时】",
            content=(
                f"【退款审核超时】{applicant}的{kind_label}申请（¥{r.amount}）"
                f"已提交超过 {hours} 小时仍未审核，请尽快处理"
            ),
            ref_type=AdminNotification.REF_REFUND_REQUEST,
            ref_id=str(r.id),
            applicant_name=applicant,
            amount=r.amount,
        )
        if ok:
            sent += 1
    if rows:
        db.commit()
    return sent


def withdrawal_review_timeout_warn(db: Session) -> int:
    """退会申请待审超过 `withdrawal_review_timeout_days` 天 → 逐单一条管理待办提醒。

    只认 `applying`（待审核）——审核通过之后的"结算/退款卡住"另有链路兜底
    （押金待结清跟进 `SCENE_DEPOSIT_SETTLE` + 退款执行失败 `SCENE_REFUND_EXECUTE_FAILED`），
    本任务不重复插手，避免同一件事在待办墙上出现两次。
    """
    days = max(
        1,
        ConfigService(db).get_int(
            "withdrawal_review_timeout_days", DEFAULT_WITHDRAWAL_TIMEOUT_DAYS
        ),
    )
    deadline = datetime.now() - timedelta(days=days)
    rows = (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.status == WithdrawalRequest.STATUS_APPLYING,
            WithdrawalRequest.created_at <= deadline,
            WithdrawalRequest.is_deleted == 0,
        )
        .all()
    )
    svc = AdminNotifyService(db)
    sent = 0
    for r in rows:
        applicant = _applicant_name(db, r.child_id)
        ok = svc.send(
            scene=AdminNotification.SCENE_WITHDRAWAL_REVIEW_TIMEOUT,
            title="【退会审核超时】",
            content=(
                f"【退会审核超时】{applicant}的退会申请已提交超过 {days} 天仍未审核，请尽快处理"
            ),
            ref_type=AdminNotification.REF_WITHDRAWAL_REQUEST,
            ref_id=str(r.id),
            applicant_name=applicant,
        )
        if ok:
            sent += 1
    if rows:
        db.commit()
    return sent
