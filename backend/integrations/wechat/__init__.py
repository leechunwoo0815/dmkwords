# backend/integrations/wechat/__init__.py
"""微信集成层 — 登录 / 支付 V3 / 订阅消息"""

from backend.integrations.wechat.auth import WeChatAuth
from backend.integrations.wechat.config import PayDescription, SubscribeTemplate
from backend.integrations.wechat.pay_v3 import WeChatPayV3
from backend.integrations.wechat.subscribe import WeChatSubscribe

__all__ = [
    "WeChatAuth",
    "WeChatPayV3",
    "WeChatSubscribe",
    "SubscribeTemplate",
    "PayDescription",
]
