import asyncio
import json
import logging
import random
import time

from backend.common.gateways.sms.base import SmsGateway
from backend.common.gateways.sms.types import SmsSendRequest, SmsSendResponse

logger = logging.getLogger(__name__)


def _mask_phone(phone: str) -> str:
    """F-020：手机号日志脱敏（138****0000）"""
    if not phone or len(phone) < 7:
        return phone or ""
    return phone[:3] + "****" + phone[-4:]


try:
    from alibabacloud_dysmsapi20170525 import models as dysmsapi_models
    from alibabacloud_dysmsapi20170525.client import Client as DysmsapiClient
    from alibabacloud_tea_openapi import models as open_api_models

    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class AliyunSmsGateway(SmsGateway):
    """阿里云短信网关

    依赖：pip install alibabacloud-dysmsapi20170525 alibabacloud-tea-openapi
    配置：SMS_APP_ID=AccessKeyId, SMS_APP_KEY=AccessKeySecret
    文档：https://help.aliyun.com/document_detail/101414.html

    注：Aliyun SDK 为同步实现，通过 asyncio.to_thread 包装避免阻塞事件循环。
    """

    _SMS_TTL = 300
    _codes: dict[str, tuple[str, float]] = {}

    def __init__(self, app_id: str, app_key: str, sign_name: str, template_code: str):
        self.app_id = app_id
        self.app_key = app_key
        self.sign_name = sign_name
        self.template_code = template_code
        self._client = None

    def _get_client(self):
        if self._client is None:
            config = open_api_models.Config(
                access_key_id=self.app_id,
                access_key_secret=self.app_key,
            )
            config.endpoint = "dysmsapi.aliyuncs.com"
            self._client = DysmsapiClient(config)
        return self._client

    def _call_send_sms(self, req) -> tuple[bool, str]:
        try:
            resp = self._get_client().send_sms(req)
            if resp.body and resp.body.code == "OK":
                return True, ""
            return False, resp.body.message if resp.body else "未知错误"
        except Exception as e:
            logger.error("阿里云 SMS SDK 异常 %s", e, exc_info=True)
            return False, str(e)

    async def send_code(self, phone: str) -> SmsSendResponse:
        code = f"{random.randint(100000, 999999)}"

        if not _HAS_SDK or not self.app_id or not self.app_key:
            self._codes[phone] = (code, time.time())
            logger.info(
                "[AliyunSms(dev)] 验证码 %s 已生成（SDK/凭据未配置，未实际发送）phone=%s",
                code[:4],
                _mask_phone(phone),
            )
            return SmsSendResponse(success=True, code=code)

        req = dysmsapi_models.SendSmsRequest(
            phone_numbers=phone,
            sign_name=self.sign_name,
            template_code=self.template_code,
            template_param=json.dumps({"code": code}),
        )
        ok, err = await asyncio.to_thread(self._call_send_sms, req)
        if ok:
            self._codes[phone] = (code, time.time())
            logger.info("阿里云 SMS 验证码已发送 phone=%s", _mask_phone(phone))
        else:
            logger.error("阿里云 SMS 发送失败 phone=%s reason=%s", _mask_phone(phone), err)
        return SmsSendResponse(success=ok, error_message=err, code=code)

    async def verify_code(self, phone: str, code: str) -> bool:
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
        if not _HAS_SDK or not self.app_id or not self.app_key:
            logger.info(
                "[AliyunSms(dev)] 通知短信 phone=%s template=%s params=%s（SDK/凭据未配置）",
                _mask_phone(request.phone),
                request.template_id,
                request.template_params,
            )
            return SmsSendResponse(success=True)

        template_params = json.dumps(request.template_params) if request.template_params else None
        req = dysmsapi_models.SendSmsRequest(
            phone_numbers=request.phone,
            sign_name=self.sign_name,
            template_code=request.template_id or self.template_code,
            template_param=template_params,
        )
        ok, err = await asyncio.to_thread(self._call_send_sms, req)
        logger.info(
            "阿里云 SMS 通知 %s phone=%s",
            "成功" if ok else "失败",
            _mask_phone(request.phone),
        )
        return SmsSendResponse(success=ok, error_message=err)
