# backend/integrations/wechat/auth.py
"""微信登录集成 — 统一使用 BusinessException"""

import logging

import httpx

from backend.common.exceptions import ForbiddenError, ValidationError
from backend.config import get_settings

logger = logging.getLogger(__name__)


class WeChatAuth:
    """微信登录 — code 换 openid、手机号"""

    CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

    @staticmethod
    async def code_to_session(code: str) -> dict:
        """用 code 换取 openid 和 session_key

        Raises:
            ValidationError: code 无效或过期
            ForbiddenError: 微信接口返回权限错误
        """
        settings = get_settings()
        params = {
            "appid": settings.WECHAT_APP_ID,
            "secret": settings.WECHAT_APP_SECRET,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(WeChatAuth.CODE2SESSION_URL, params=params)
            data = resp.json()

        errcode = data.get("errcode", 0)
        if errcode != 0:
            errmsg = data.get("errmsg", "未知错误")
            if errcode in (40029, 40163):
                # code 无效或已使用
                raise ValidationError(f"微信登录失败: code无效或已过期 ({errmsg})")
            elif errcode in (40013, 40125):
                # appid/secret 配置错误
                raise ForbiddenError(f"微信登录配置错误: {errmsg}")
            else:
                raise ValidationError(f"微信登录失败: {errmsg}")

        return data

    @staticmethod
    async def get_phone_number(phone_code: str) -> str | None:
        """通过微信临时 code 获取用户手机号"""
        from backend.integrations.wechat.subscribe import _get_access_token

        token = await _get_access_token()
        token_str = token.get("access_token")
        if not token_str:
            return None
        url = f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token_str}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"code": phone_code}, timeout=5)
            data = resp.json()
            if data.get("errcode") == 0:
                return data["phone_info"]["phoneNumber"]
            logger.warning(
                "Get phone number failed: errcode=%s, errmsg=%s",
                data.get("errcode"),
                data.get("errmsg"),
            )
            return None
