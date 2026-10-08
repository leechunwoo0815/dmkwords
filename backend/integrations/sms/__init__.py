# backend/integrations/sms/__init__.py — 短信网关选择器（2026-10-08 接线）
"""按 `SMS_PROVIDER` 选网关：mock（开发）/ aliyun / tencent（生产）。

为什么要有这个选择器：`aliyun.py` / `tencent.py` 两个实现早就写好了，但**没有任何地方按厂商选**
（文件头自注"无人 import"）——生产环境只能走 Mock，短信永远发不出去（上线前审查 P0-1 的伴生问题）。
现在这里是唯一入口：`get_sms_gateway()`。

网关只负责**生成并发送**验证码；校验由 `backend/domain/identity/sms_service.py` 用库里的哈希做
（多 worker 一致、可限流、可审计）。所以本模块不暴露 `verify_code` 的使用。
"""

from __future__ import annotations

from functools import lru_cache

from backend.common.gateways.sms.base import SmsGateway
from backend.config import get_settings


@lru_cache
def get_sms_gateway() -> SmsGateway:
    """当前生效的短信网关（进程内单例）。"""
    settings = get_settings()
    provider = (settings.SMS_PROVIDER or "mock").strip().lower()
    if provider == "mock":
        from backend.common.gateways.sms.mock import MockSmsGateway

        return MockSmsGateway()
    if provider == "aliyun":
        from backend.integrations.sms.aliyun import AliyunSmsGateway

        return AliyunSmsGateway(
            app_id=settings.SMS_APP_ID,
            app_key=settings.SMS_APP_KEY,
            sign_name=settings.SMS_SIGN_NAME,
            template_code=settings.SMS_TEMPLATE_CODE,
        )
    if provider == "tencent":
        from backend.integrations.sms.tencent import TencentSmsGateway

        return TencentSmsGateway(
            app_id=settings.SMS_APP_ID,
            app_key=settings.SMS_APP_KEY,
            sign_name=settings.SMS_SIGN_NAME,
            template_code=settings.SMS_TEMPLATE_CODE,
        )
    raise RuntimeError(f"未知的 SMS_PROVIDER: {provider}（可选 mock/aliyun/tencent）")


def reset_sms_gateway_cache() -> None:
    """测试/换配置后清缓存（线上改 .env 需重启进程，这里只服务测试）。"""
    get_sms_gateway.cache_clear()
