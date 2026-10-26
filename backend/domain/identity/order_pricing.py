# backend/domain/identity/order_pricing.py — 会员类订单定价（**唯一来源**）
"""从 `order_service.py` 抽出（2026-10-17 第六十六批：god-file ≤800 上限，order_service 到 836 行）。

**为什么定价必须只有一份**：管理端造单与小程序在线下单都调它——二孩 9 折（V1.1 §3.1，下单时刻
判定）、99 元首场每账号一次（R-321）、观察期费资格、押金读标准配置（不可自输）。两处各写一份
必然漂移，且漂移的是**钱**。

`price_member_order` 同时是这些资格闸的**落点**（抛 `ConflictError` → 购买页 `plans()` 捕获后置灰，
家长看得到"为什么不能买"）：
- 99 元首场：存在未被全额退款的已付单 → 拒；
- 观察期费（第四轮大审查 P1-1）：已是会员关系（观察期/待评估/正式）→ 拒（状态机判据，不另写状态表）；
- 年费：账号下另有有效会员孩子 → 二孩折扣；
- 押金：读标准配置。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func

from backend.common.exceptions import ConflictError, ValidationError
from backend.domain.identity.models import Child, Order, Parent


def _config_decimal(db, key: str) -> Decimal:
    from backend.common.config_service import ConfigService

    return Decimal(ConfigService(db).get_value(key))


def price_member_order(db, parent: Parent, child: Child, order_type: str) -> Decimal:
    """会员类订单定价（**唯一来源**：管理端造单与管理小程序在线下单都走它）。

    - 99 元首场（R-321）：每账号一次，存在未被全额退款的已付单则拒绝
    - 观察期费：**购买资格**（须能转入 observation）+ 读配置
    - 年费：下单时刻该账号另有有效会员孩子 → 二孩折扣（V1.1 §3.1，有效=日期感知口径 D1）
    - 押金：读标准配置不可自输（R3/FEAT-080；确认收款时联动建 Deposit+Ledger）
    """
    if order_type == Order.TYPE_FIRST_ACTIVITY:
        exists = (
            db.query(func.count(Order.id))
            .filter(
                Order.parent_id == parent.id,
                Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                Order.status == Order.STATUS_PAID,
                Order.refund_status != Order.REFUND_STATUS_REFUNDED,
                Order.is_deleted == 0,
            )
            .scalar()
        )
        if exists:
            raise ConflictError("该账号已购买过首场亲子活动（每账号仅一次）")
        return _config_decimal(db, "first_activity_fee")
    if order_type == Order.TYPE_OBSERVATION:
        # 第四轮大审查 P1-1（2026-10-17）：**购买资格**——已是会员关系（观察期/待评估/正式）的孩子
        # 不能再买观察期费。此前这一型**没有任何资格判据**（押金、99 元首场都有），于是正式会员
        # 也能下单 500 元观察期费 → 在线付款成功 → 结算时转移矩阵拒（observation 不在允许集合里）
        # → 回调兜底「回滚 + 资金异常待办 + 200」= **钱收了、订单停待支付、只能人工退款**。
        # 判据直接问状态机（单一来源，不另写一份状态列表）；`plans()` 会捕获 ConflictError 置灰，
        # 家长在购买页直接看到原因；发起支付前还有第二道复核（`_assert_observation_payable`）。
        if not child.can_transition(Child.MEMBER_OBSERVATION):
            raise ConflictError(
                "孩子已是有效会员，如需续期请购买年度会员服务（观察期会员服务仅未入会/已过期/已退会可购）"
            )
        return _config_decimal(db, "observation_fee")
    if order_type == Order.TYPE_FORMAL:
        base = _config_decimal(db, "formal_fee")
        discount = _config_decimal(db, "second_child_discount_percent")
        siblings = (
            db.query(Child)
            .filter(
                Child.parent_id == parent.id,
                Child.id != child.id,
                Child.is_deleted == 0,
            )
            .all()
        )
        siblings_active = any(s.is_active_member for s in siblings)
        return (
            (base * discount / Decimal(100)).quantize(Decimal("0.01")) if siblings_active else base
        )
    if order_type == Order.TYPE_DEPOSIT:
        return _config_decimal(db, "deposit_amount")
    raise ValidationError("订单类型不正确")
