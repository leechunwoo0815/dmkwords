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
    PaymentRefundQuery,
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

        return PaymentRefundResponse(
            success=True,
            refund_id=refund_id,
            # mock 通道即时退（与 supports_instant_payment 同语义）：业务层据此直接落"已退款"；
            # 真通道会回 PROCESSING，那时要留在"执行中"等退款结果通知
            state="SUCCESS",
        )

    async def query_refund(self, out_refund_no: str) -> PaymentRefundQuery:
        """mock 查单：即时退款语义 → 一律"已受理且成功"（与 refund() 的 state=SUCCESS 一致）。

        未知态演练用 stub 网关（tests/unit/test_wm12_review_fixes.py），不靠 mock 造。
        """
        logger.info("[MockPay] 退款查单 out_refund_no=%s → SUCCESS（mock 即时退）", out_refund_no)
        return PaymentRefundQuery(
            found=True, state="SUCCESS", refund_id=f"mock_refund_{uuid.uuid4().hex[:16]}"
        )

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
            refund_raw = data.get("refund_amount")
            refund_amount = (
                Decimal(str(refund_raw)) / Decimal("100") if refund_raw is not None else None
            )
            return PaymentCallbackData(
                out_trade_no=data.get("out_trade_no", ""),
                out_refund_no=data.get("out_refund_no", ""),
                transaction_id=data.get("transaction_id", f"mock_txn_{uuid.uuid4().hex[:16]}"),
                # 2026-10-08 WM12-A：trade_state 从报文取（默认 SUCCESS）——验收 S4/S6 要演练
                # 「非成功状态不入账」「退款单忽略支付回调」，写死 SUCCESS 就没法演练了
                trade_state=data.get("trade_state", "SUCCESS"),
                refund_status=data.get("refund_status", ""),
                amount=amount,
                # WM12-C（审查 P1-7）：与微信 `amount.refund` 同语义（分 → 元）；缺省=None 表示
                # 报文没带钱数（老报文/演练）——此时不做金额比对，但会记审计
                refund_amount=refund_amount,
                raw_body=ciphertext,
            )
        except json.JSONDecodeError as e:
            raise PaymentException("Mock 回调数据格式错误") from e
