# backend/common/gateways/payment/mock.py
"""Mock 支付网关 — 本地开发使用

特性：
- 下单返回符合微信小程序 wx.requestPayment 格式的 mock 参数
- 退款返回成功
- 回调验签始终通过
- 控制台打印完整订单/退款信息
"""

import json
import logging
import time
import uuid
from decimal import Decimal

from backend.common.gateways.exceptions import PaymentException
from backend.common.gateways.payment.base import PaymentGateway
from backend.common.gateways.payment.types import (
    PaymentCallbackData,
    PaymentOrderRequest,
    PaymentOrderResponse,
    PaymentRefundRequest,
    PaymentRefundResponse,
)

logger = logging.getLogger(__name__)


class MockPaymentGateway(PaymentGateway):
    """Mock 支付网关 — 本地开发模式"""

    @property
    def supports_instant_payment(self) -> bool:
        return True

    async def create_jsapi_order(
        self, openid: str, order_no: str, amount_cent: int, description: str
    ) -> dict:
        """JSAPI 下单 — 匹配 WeChatPayV3.create_jsapi_order 签名"""
        prepay_id = f"mock_prepay_{uuid.uuid4().hex[:16]}"
        timestamp = str(int(time.time()))
        nonce_str = uuid.uuid4().hex[:16]
        package = f"prepay_id={prepay_id}"

        pay_params = {
            "timeStamp": timestamp,
            "nonceStr": nonce_str,
            "package": package,
            "signType": "RSA",
            "paySign": f"mock_sign_{uuid.uuid4().hex[:32]}",
        }
        logger.info(
            "[MockPay] JSAPI下单 out_trade_no=%s amount_cent=%s description=%s",
            order_no,
            amount_cent,
            description,
        )
        return pay_params

    async def create_order(self, request: PaymentOrderRequest) -> PaymentOrderResponse:
        prepay_id = f"mock_prepay_{uuid.uuid4().hex[:16]}"
        timestamp = str(int(time.time()))
        nonce_str = uuid.uuid4().hex[:16]
        package = f"prepay_id={prepay_id}"

        pay_params = {
            "timeStamp": timestamp,
            "nonceStr": nonce_str,
            "package": package,
            "signType": "RSA",
            "paySign": f"mock_sign_{uuid.uuid4().hex[:32]}",
        }

        logger.info(
            "[MockPay] 下单成功 out_trade_no=%s amount=%s description=%s prepay_id=%s",
            request.out_trade_no,
            request.amount,
            request.description,
            prepay_id,
        )

        return PaymentOrderResponse(success=True, prepay_id=prepay_id, pay_params=pay_params)

    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResponse:
        refund_id = f"mock_refund_{uuid.uuid4().hex[:16]}"

        logger.info(
            "[MockPay] 退款成功 out_trade_no=%s refund_amount=%s total=%s reason=%s",
            request.out_trade_no,
            request.refund_amount,
            request.total_amount,
            request.reason or "用户申请退款",
        )

        return PaymentRefundResponse(success=True, refund_id=refund_id)

    async def verify_callback_signature(
        self, body: str, signature: str, timestamp: str, nonce: str
    ) -> bool:
        logger.info("[MockPay] 回调签名验证通过（Mock 模式始终放行）")
        return True

    async def decrypt_callback_data(
        self, ciphertext: str, nonce: str, associated_data: str
    ) -> PaymentCallbackData:
        try:
            data = json.loads(ciphertext)
            amount_raw = data.get("amount")
            amount = Decimal(str(amount_raw)) / Decimal("100") if amount_raw is not None else None
            return PaymentCallbackData(
                out_trade_no=data.get("out_trade_no", ""),
                out_refund_no=data.get("out_refund_no", ""),
                transaction_id=data.get("transaction_id", f"mock_txn_{uuid.uuid4().hex[:16]}"),
                trade_state="SUCCESS",
                amount=amount,
                raw_body=ciphertext,
            )
        except json.JSONDecodeError as e:
            raise PaymentException("Mock 回调数据格式错误") from e
