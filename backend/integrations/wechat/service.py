# backend/integrations/wechat/service.py — 微信服务（access_token 统一管理）
"""access_token 获取与进程内缓存（双重检查锁），供订阅消息/登录等共享。

原 backend.domain.wechat.service 目录不存在（死代码断裂点）——WM11 重建于
integrations 层：8 域已定（ADR-002），微信是外部集成，不新增域。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"


class WeChatService:
    """微信集成服务：access_token 获取 + 进程内缓存。"""

    _token: str | None = None
    _token_expires_at: datetime | None = None
    _lock = threading.Lock()

    def get_access_token(self) -> str | None:
        """获取 access_token（缓存 110 分钟，双检查锁防并发刷新）。"""
        if self._token and self._token_expires_at and self._token_expires_at > datetime.now():
            return self._token

        with self._lock:
            if self._token and self._token_expires_at and self._token_expires_at > datetime.now():
                return self._token

            settings = get_settings()
            if not settings.WECHAT_APP_ID or not settings.WECHAT_APP_SECRET:
                logger.warning("wechat credentials not configured, skip access_token fetch")
                return None
            try:
                resp = httpx.get(
                    TOKEN_URL,
                    params={
                        "grant_type": "client_credential",
                        "appid": settings.WECHAT_APP_ID,
                        "secret": settings.WECHAT_APP_SECRET,
                    },
                    timeout=10,
                )
                data = resp.json()
                token = data.get("access_token")
                if not token:
                    logger.error("wechat access_token fetch failed: %s", data)
                    return None
                self._token = token
                self._token_expires_at = (
                    datetime.now()
                    + timedelta(seconds=int(data["expires_in"]))
                    - timedelta(minutes=10)
                )
                return token
            except Exception as exc:
                logger.error("wechat access_token fetch exception: %s", exc)
                return None
