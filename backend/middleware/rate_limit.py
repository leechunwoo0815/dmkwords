# backend/middleware/rate_limit.py
"""简单内存限流中间件

用于保护 login/payment 等敏感端点免受暴力攻击。
生产环境建议替换为 Redis 限流方案。
"""

import logging
import time
from collections import defaultdict

from fastapi import Request

from backend.common.exceptions import RateLimitError

logger = logging.getLogger(__name__)


class RateLimiter:
    """基于滑动窗口的内存限流器"""

    def __init__(self):
        # {key: [timestamp, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        """检查是否超过频率限制，超过则抛 RateLimitError"""
        now = time.time()
        cutoff = now - window_seconds

        # 清理过期记录
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

        if len(self._requests[key]) >= max_requests:
            logger.warning(
                "Rate limit hit: key=%s, max=%d, window=%ds",
                key,
                max_requests,
                window_seconds,
            )
            raise RateLimitError(f"请求过于频繁，请 {window_seconds} 秒后再试")

        self._requests[key].append(now)


# 全局限流器实例
_limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    """优先取 X-Forwarded-For 首个 IP（反代后 client.host 退化为代理 IP 导致全站共享限流桶）

    注：信任 XFF 假设网关剥离了不可信头（Nginx proxy_set_header 追加时客户端可伪造首段），
    生产部署需网关覆盖，否则攻击者可自填 XFF 伪造独立 IP 绕过单 IP 桶。
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """限流依赖注入

    用法：
        @router.post("/login", dependencies=[Depends(rate_limit(5, 60))])
    """

    def _check(request: Request):
        key = f"{request.url.path}:{_client_ip(request)}"
        _limiter.check(key, max_requests, window_seconds)

    return _check
