# backend/common/gateways/sms/types.py
"""短信网关数据结构"""

from dataclasses import dataclass


@dataclass
class SmsSendRequest:
    phone: str
    template_id: str = ""
    template_params: dict | None = None


@dataclass
class SmsSendResponse:
    success: bool
    code: str = ""
    error_message: str = ""


@dataclass
class SmsVerifyRequest:
    phone: str
    code: str
