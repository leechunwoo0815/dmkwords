# backend/common/gateways/payment/base.py
"""支付网关抽象基类"""

from abc import ABC, abstractmethod

from backend.common.gateways.payment.types import (
    PaymentCallbackData,
    PaymentOrderRequest,
    PaymentOrderResponse,
    PaymentRefundRequest,
    PaymentRefundResponse,
)


class PaymentGateway(ABC):
    """支付网关抽象接口

    所有支付实现（微信支付 V2/V3、Mock）必须实现此接口。
    Service 层通过依赖注入获取实例，不感知具体实现。
    """

    @property
    def supports_instant_payment(self) -> bool:
        """Mock 网关即时到账（无需回调），真实网关需等待微信回调"""
        return False

    @abstractmethod
    async def create_order(self, request: PaymentOrderRequest) -> PaymentOrderResponse:
        """统一下单 — 返回小程序拉起支付所需的参数"""
        ...

    @abstractmethod
    async def refund(self, request: PaymentRefundRequest) -> PaymentRefundResponse:
        """申请退款"""
        ...

    @abstractmethod
    async def verify_callback_signature(
        self, body: str, signature: str, timestamp: str, nonce: str
    ) -> bool:
        """验证支付回调签名

        Returns:
            True 签名有效

        Raises:
            PaymentSignatureException 签名无效
        """
        ...

    @abstractmethod
    async def decrypt_callback_data(
        self, ciphertext: str, nonce: str, associated_data: str
    ) -> PaymentCallbackData:
        """解密回调通知数据"""
        ...
