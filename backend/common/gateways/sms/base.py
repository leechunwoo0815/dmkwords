# backend/common/gateways/sms/base.py
"""短信网关抽象基类"""

from abc import ABC, abstractmethod

from backend.common.gateways.sms.types import SmsSendRequest, SmsSendResponse


class SmsGateway(ABC):
    """短信网关抽象接口

    所有短信实现（阿里云、腾讯云、Mock）必须实现此接口。
    Service 层通过依赖注入获取实例，不感知具体实现。
    """

    @abstractmethod
    async def send_code(self, phone: str) -> SmsSendResponse:
        """发送验证码"""
        ...

    @abstractmethod
    async def verify_code(self, phone: str, code: str) -> bool:
        """校验验证码"""
        ...

    @abstractmethod
    async def send_notification(self, request: SmsSendRequest) -> SmsSendResponse:
        """发送业务通知短信"""
        ...
