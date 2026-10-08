# backend/integrations/payment/__init__.py — 支付网关选择器（2026-10-08 WM12-A 接线）
"""按 `PAYMENT_PROVIDER` 选网关：mock（开发/演示，即时到账）/ wechat（微信支付 V3）。

为什么要有这个选择器：`backend/integrations/wechat/pay_v3.py` 的 `WeChatPayV3` 早就写好了
（下单/验签/解密/退款/证书轮换全都有），但**没有任何地方 import 它**；`MockPaymentGateway`
同样**一个调用方都没有**——两端都是死代码，等于线上支付从未接通过（上线前审查 P0-2）。

现在这里是唯一入口：`get_payment_gateway()`。生产禁 mock（`validate_production` 拒启）：
mock 的 `verify_callback_signature` 永远返回 True，生产用它等于"任何订单都能被伪造回调置为已支付"。
"""

from __future__ import annotations

from functools import lru_cache

from backend.common.gateways.payment.base import PaymentGateway
from backend.config import get_settings


@lru_cache
def get_payment_gateway() -> PaymentGateway:
    """当前生效的支付网关（进程内单例）。"""
    settings = get_settings()
    provider = (settings.PAYMENT_PROVIDER or "mock").strip().lower()
    if provider == "mock":
        from backend.common.gateways.payment.mock import MockPaymentGateway

        return MockPaymentGateway()
    if provider == "wechat":
        from backend.integrations.wechat.pay_v3 import WeChatPayV3

        return WeChatPayV3()
    raise RuntimeError(f"未知的 PAYMENT_PROVIDER: {provider}（可选 mock/wechat）")


def reset_payment_gateway_cache() -> None:
    """测试/换配置后清缓存（线上改 .env 需重启进程，这里只服务测试）。"""
    get_payment_gateway.cache_clear()
