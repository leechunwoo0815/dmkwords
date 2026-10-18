# backend/middleware/rate_limit.py
"""限流（两档）

① **逐端点装饰器** `rate_limit(...)`：登录/支付/验证码等敏感点，按"路径+IP"收紧；
② **全局写中间件** `WriteRateLimitMiddleware`（第七轮 P2-6）：全体写端点，按"路径类+身份"限流。

内存实现（单进程）。生产多副本时每副本独立计数，需要全局配额应换 Redis 方案。
"""

import hashlib
import logging
import re
import time
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.common.exceptions import RateLimitError

logger = logging.getLogger(__name__)


class RateLimiter:
    """基于滑动窗口的内存限流器"""

    #: 惰性清扫间隔（每 N 次检查清一次）：全局限流把键空间从"6 个端点"扩到
    #: "全体身份 × 路径类"——不清理等于自己制造内存增长（第七轮 P2-6）。
    _SWEEP_EVERY = 512
    #: 桶的陈旧阈值（秒）：大于所有调用点窗口（现最大 3600），超过即可安全回收。
    _STALE_AFTER = 3600

    def __init__(self):
        # {key: [timestamp, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._checks = 0

    def check(self, key: str, max_requests: int, window_seconds: int) -> None:
        """检查是否超过频率限制，超过则抛 RateLimitError"""
        now = time.time()
        cutoff = now - window_seconds

        # 清理过期记录
        recent = [t for t in self._requests.get(key, ()) if t > cutoff]

        if len(recent) >= max_requests:
            self._requests[key] = recent
            logger.warning(
                "Rate limit hit: key=%s, max=%d, window=%ds",
                key,
                max_requests,
                window_seconds,
            )
            raise RateLimitError(f"请求过于频繁，请 {window_seconds} 秒后再试")

        recent.append(now)
        self._requests[key] = recent
        self._sweep(now)

    def _sweep(self, now: float) -> None:
        """惰性清扫：窗口外的陈旧桶整键回收（键数有界，内存不随历史身份/路径累积）。"""
        self._checks += 1
        if self._checks % self._SWEEP_EVERY:
            return
        stale = now - self._STALE_AFTER
        for key in [k for k, v in self._requests.items() if not v or max(v) < stale]:
            del self._requests[key]


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


# ---------- 全局写限流（第七轮 P2-6） ----------

#: 写方法：只拦这四种。GET/HEAD/OPTIONS 是读——媒体流、大屏轮询、列表页不受影响。
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 豁免前缀（写方法才走到这里）：
#:   `/health`  健康检查（探针高频，非业务写）
#:   `/api/pay/` 微信支付/退款回调——**微信会重试**，限流会把回调挡在门外 = 丢单
#:   `/docs` `/redoc` `/openapi.json`  FastAPI 内建文档（非业务写；生产本就关闭）
_EXEMPT_PREFIXES = ("/health", "/api/pay/", "/docs", "/redoc", "/openapi.json")

#: 路径归一化：数字段 / 长十六进制段（资源 id、uuid、hex token）→ `{id}`。
#: 不归一化 = 换 id 就换桶（`/books/1/cover` 与 `/books/2/cover` 各算一份预算 = 形同虚设），
#: 且桶数随资源数无界增长（内存放大）。
_PATH_PARAM_RE = re.compile(r"/(?:\d+|[0-9a-fA-F]{16,})(?=/|$)")

#: 更紧的路径类预算 (前缀, 后缀, (max, window))：按"单次请求的资源开销"收紧——
#: 批量导入一次 1000 行吃内存、上传类单次落盘吃磁盘；其余写走配置默认值。
_WRITE_BUDGET_RULES: tuple[tuple[str, str, tuple[int, int]], ...] = (
    ("/api/admin/books/import", "", (6, 60)),
    ("/api/admin/books/", "/cover", (20, 60)),
    ("/api/admin/books/", "/audio", (20, 60)),
    ("/api/admin/activities/", "/cover", (20, 60)),
    ("/api/admin/activities/", "/detail-images", (20, 60)),
    ("/api/miniapp/children/", "/avatar", (20, 60)),
)


def normalize_path(path: str) -> str:
    """限流分桶用的路径类（资源 id 归一化成 `{id}`）。"""
    return _PATH_PARAM_RE.sub("/{id}", path)


def _identity(request: Request) -> str:
    """限流身份：**优先登录态**——`Authorization` 头的 sha1 摘要前 16 位
    （不把 token 原文留在内存键/日志里）；无头回落 `_client_ip`（只信传输层，口径同登录限流）。
    """
    auth = (request.headers.get("authorization") or "").strip()
    if auth:
        return "tok:" + hashlib.sha1(auth.encode("utf-8")).hexdigest()[:16]
    return "ip:" + _client_ip(request)


def _write_budget(path: str) -> tuple[int, int]:
    """该写路径类的预算 (max_requests, window_seconds)：命中更紧规则用规则值，否则用配置默认。"""
    for prefix, suffix, budget in _WRITE_BUDGET_RULES:
        if path.startswith(prefix) and (not suffix or path.endswith(suffix)):
            return budget
    from backend.config import get_settings

    return int(getattr(get_settings(), "RATE_LIMIT_WRITE_PER_MINUTE", 60)), 60


class WriteRateLimitMiddleware(BaseHTTPMiddleware):
    """全局写限流：**一处生效，全体写端点自动覆盖**。

    为什么用中间件而不是逐端点挂装饰器：写端点 111 个且持续新增，逐处挂必漏
    （第七轮实测：110 个写端点只有 6 处挂了 `rate_limit`）。判据只看"是不是写方法 +
    路径类"，与路由定义无关，新增端点自动纳入。

    响应与 `business_exception_handler` 同形（`{"detail": ...}` + `Retry-After`）：
    这里**不能 raise** —— 本中间件在 ExceptionMiddleware 之外，raise 只会变成 500；
    同形还让两端前端（`request.js` / `client.ts`）按 `detail` 弹提示，**零前端改动**。
    """

    async def dispatch(self, request: Request, call_next):
        path = normalize_path(request.url.path)
        if request.method not in _WRITE_METHODS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        max_requests, window_seconds = _write_budget(path)
        if max_requests <= 0:  # 0 = 关闸（压测/排障；生产禁）
            return await call_next(request)

        key = f"write:{path}:{_identity(request)}"
        try:
            _limiter.check(key, max_requests, window_seconds)
        except RateLimitError as exc:
            return JSONResponse(
                status_code=exc.code,
                content={"detail": exc.message},
                headers={"Retry-After": str(window_seconds)},
            )
        return await call_next(request)
