# backend/common/gateways/payment/types.py
"""支付网关数据结构 — 业务层与网关层的数据契约

⚠️ 金额单位铁律：PaymentOrderRequest.amount / PaymentRefundRequest.total_amount /
refund_amount 一律为【分】（微信支付契约）。业务层拿到元后必须经 yuan_to_cents() 转换，
禁止直接传元（F1/F2 元分混淆教训：generate_pay_code 与三处退款曾传元，500 元生成 5 元支付单）。
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal


def yuan_to_cents(amount: Decimal) -> int:
    """元 → 分（支付网关契约单位，四舍五入到分，与微信支付一致）。"""
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class PaymentOrderRequest:
    """下单请求 — amount 单位为【分】"""

    out_trade_no: str
    amount: Decimal
    description: str
    openid: str = ""
    attach: str = ""


@dataclass
class PaymentOrderResponse:
    success: bool
    prepay_id: str = ""
    pay_params: dict = field(default_factory=dict)
    error_message: str = ""


@dataclass
class PaymentRefundRequest:
    """退款请求 — total_amount / refund_amount 单位为【分】"""

    out_trade_no: str
    refund_amount: Decimal
    total_amount: Decimal
    reason: str = ""
    out_refund_no: str = ""
    notify_url: str = ""  # 微信 V3 退款结果通知 URL（F55）


@dataclass
class PaymentRefundResponse:
    success: bool
    refund_id: str = ""
    error_message: str = ""


@dataclass
class PaymentCallbackData:
    out_trade_no: str
    out_refund_no: str = ""  # 退款结果通知的退款单号（F76-P2：区分部分退款/全额退款回调）
    transaction_id: str = ""
    trade_state: str = ""
    refund_status: str = ""  # 退款通知状态（微信 v3: SUCCESS/CLOSED/ABNORMAL/PROCESSING）
    amount: Decimal | None = None
    raw_body: str = ""
