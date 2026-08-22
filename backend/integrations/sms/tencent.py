import asyncio
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
    from tencentcloud.common import credential
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
        TencentCloudSDKException,
    )
    from tencentcloud.sms.v20210111 import models, sms_client

    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False


class TencentSmsGateway(SmsGateway):
    """腾讯云短信网关

    依赖：pip install tencentcloud-sdk-python
    配置：SMS_APP_ID=SecretId, SMS_APP_KEY=SecretKey
          SMS_APP_ID 同时也是 SmsSdkAppId
    文档：https://cloud.tencent.com/document/product/382

    注：腾讯云 SDK 为同步实现，通过 asyncio.to_thread 包装避免阻塞事件循环。
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
            cred = credential.Credential(self.app_id, self.app_key)
            self._client = sms_client.SmsClient(cred)
        return self._client

    def _call_send(self, req) -> tuple[bool, str]:
        try:
            resp = self._get_client().SendSms(req)
            if resp.SendStatusSet and resp.SendStatusSet[0].Code == "Ok":
                return True, ""
            err = resp.SendStatusSet[0].Message if resp.SendStatusSet else "未知错误"
            logger.error("腾讯云 SMS 发送失败 reason=%s", err)
            return False, err
        except TencentCloudSDKException as e:
            logger.error("腾讯云 SMS SDK 异常 %s", e, exc_info=True)
            return False, str(e)

    async def send_code(self, phone: str) -> SmsSendResponse:
        code = f"{random.randint(100000, 999999)}"

        if not _HAS_SDK or not self.app_id or not self.app_key:
            self._codes[phone] = (code, time.time())
            logger.info(
                "[TencentSms(dev)] 验证码 %s 已生成（SDK/凭据未配置，未实际发送）phone=%s",
                code[:4],
                _mask_phone(phone),
            )
            return SmsSendResponse(success=True, code=code)

        req = models.SendSmsRequest()
        req.SmsSdkAppId = self.app_id
        req.SignName = self.sign_name
        req.TemplateId = self.template_code
        req.PhoneNumberSet = [f"+86{phone}"]
        req.TemplateParamSet = [code]

        ok, err = await asyncio.to_thread(self._call_send, req)
        if ok:
            self._codes[phone] = (code, time.time())
            logger.info("腾讯云 SMS 验证码已发送 phone=%s", _mask_phone(phone))
        else:
            logger.error("腾讯云 SMS 发送失败 phone=%s reason=%s", _mask_phone(phone), err)
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
                "[TencentSms(dev)] 通知短信 phone=%s template=%s params=%s（SDK/凭据未配置）",
                _mask_phone(request.phone),
                request.template_id,
                request.template_params,
            )
            return SmsSendResponse(success=True)

        req = models.SendSmsRequest()
        req.SmsSdkAppId = self.app_id
        req.SignName = self.sign_name
        req.TemplateId = request.template_id or self.template_code
        req.PhoneNumberSet = [f"+86{request.phone}"]
        if request.template_params:
            req.TemplateParamSet = list(request.template_params.values())

        ok, err = await asyncio.to_thread(self._call_send, req)
        logger.info(
            "腾讯云 SMS 通知 %s phone=%s",
            "成功" if ok else "失败",
            _mask_phone(request.phone),
        )
        return SmsSendResponse(success=ok, error_message=err)
