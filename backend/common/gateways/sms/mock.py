# backend/common/gateways/sms/mock.py
"""Mock 短信网关 — 本地开发使用

特性：
- 内存存储验证码（线程安全），5 分钟自动失效
- 控制台打印验证码，方便测试
- 提供静态方法 get_code(phone) 供测试读取验证码
- 业务通知仅日志输出
"""

import logging
import random
import threading
import time

from backend.common.gateways.sms.base import SmsGateway
from backend.common.gateways.sms.types import SmsSendRequest, SmsSendResponse

logger = logging.getLogger(__name__)


class MockSmsGateway(SmsGateway):
    """Mock 短信网关 — 本地开发模式"""

    _SMS_TTL = 300

    _codes: dict[str, tuple[str, float]] = {}
    _lock = threading.Lock()

    async def send_code(self, phone: str) -> SmsSendResponse:
        code = f"{random.randint(100000, 999999)}"
        with self._lock:
            self._codes[phone] = (code, time.time())

        logger.info(
            "[MockSms] 验证码已生成 phone=%s code=%s**（开发模式，未实际发送）",
            phone,
            code[:4],
        )

        return SmsSendResponse(success=True, code=code)

    async def verify_code(self, phone: str, code: str) -> bool:
        with self._lock:
            stored = self._codes.get(phone)
            if not stored:
                return False
            if time.time() - stored[1] > self._SMS_TTL:
                self._codes.pop(phone, None)
                return False
            if stored[0] != code:
                return False
            self._codes.pop(phone, None)
            return True

    async def send_notification(self, request: SmsSendRequest) -> SmsSendResponse:
        logger.info(
            "[MockSms] 通知短信 phone=%s template=%s params=%s（开发模式，未实际发送）",
            request.phone,
            request.template_id,
            request.template_params,
        )
        return SmsSendResponse(success=True)

    @classmethod
    def get_code(cls, phone: str) -> str | None:
        """获取指定手机号的验证码（仅供测试使用）"""
        with cls._lock:
            stored = cls._codes.get(phone)
            if stored and time.time() - stored[1] <= cls._SMS_TTL:
                return stored[0]
            return None
