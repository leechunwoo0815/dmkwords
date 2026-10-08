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
    """限流客户端 IP：**默认只信传输层**（`request.client.host`）。

    WM12-C（审查 P1-5）：原实现无条件取 `X-Forwarded-For` 首段——客户端自填
    `X-Forwarded-For: 10.9.1.x` 就能每个请求换一个限流桶（实测 20 个并发伪造 XFF 全部
    通过 `3/60` 的登录限流）。现在只有 `TRUSTED_PROXY_COUNT>0`（显式声明"我前面有几层
    可信代理"）才读 XFF，且取**右起第 N 段**：每层可信代理会在右侧追加它亲眼看到的上游
    地址，最右段来自最近的代理，客户端伪造的左侧段一律不采信（docs/20 §五/§八）。
    """
    from backend.config import get_settings

    trusted = int(getattr(get_settings(), "TRUSTED_PROXY_COUNT", 0) or 0)
    if trusted > 0:
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        if len(parts) >= trusted:
            return parts[-trusted]
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
