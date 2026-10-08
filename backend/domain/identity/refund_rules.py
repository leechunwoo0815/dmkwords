# backend/domain/identity/refund_rules.py — 退款口径（单一来源）2026-10-08 抽出
"""**可退金额 / 剩余天数 / 说明文案**三件事的唯一实现。

为什么单独成模块（两重原因）：
① 口径同源（上线前审查 P2-3）：可退金额的分母与"说明文案里的天数"必须来自同一份配置
   （`observation_period_days` / `formal_period_days`），否则改口径后卡片上的折算过程会撒谎；
② `wm10_service.py` 触发架构关"god file 禁止"（>800 行）——把这块自洽的规则抽出来，
   服务类只保留流程编排（预览/申请/审核/执行）。

三条退款口径（与 PRD 一致，改这里 = 改全站）：
  观察期费：按剩余天数比例（分母 = 观察期时长，配置）
  年费：按剩余天数比例（分母 = 年费时长，配置）
  首场活动费 / 活动费：未签到未开始全额（是否可退由人工审核判断）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.domain.identity.models import Order


def period_days(db: Session, order_type: str) -> int:
    """该订单类型对应的会员期限（天）——开通时长与退款分母**同一份配置**。"""
    if order_type == Order.TYPE_OBSERVATION:
        return int(ConfigService(db).get_value("observation_period_days", "30"))
    return int(ConfigService(db).get_value("formal_period_days", "365"))


def days_used(order: Order) -> int:
    """已使用天数（按付款时刻起算；未付款为 0）。"""
    if not order.paid_at:
        return 0
    return max(0, (datetime.now() - order.paid_at).days)


def refundable_amount(db: Session, order: Order) -> Decimal:
    """可退金额（服务端唯一权威）。"""
    if order.order_type in (Order.TYPE_OBSERVATION, Order.TYPE_FORMAL):
        period = period_days(db, order.order_type)
        remaining = max(0, period - days_used(order))
        return (order.amount * Decimal(remaining) / Decimal(period)).quantize(Decimal("0.01"))
    # 首场活动费 / 活动费：未签到未开始全额（是否可退由审核判断）
    return order.amount


def rule_text(db: Session, order: Order) -> str:
    """给审核员看的规则说明（与实退算法同源，改配置后文字自动跟着变）。"""
    if order.order_type == Order.TYPE_OBSERVATION:
        return f"观察期费按剩余天数比例退（{period_days(db, order.order_type)} 天期，无手续费）"
    if order.order_type == Order.TYPE_FORMAL:
        return "年费按剩余天数比例退（按实付金额）"
    if order.order_type == Order.TYPE_FIRST_ACTIVITY:
        return "未参加全额退（已参加过不退，审核时核对）"
    return "活动费：未签到且未开始全额退"
