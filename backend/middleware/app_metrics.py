# backend/middleware/app_metrics.py — 应用指标采集中间件
"""只统计 `/api/*`，排除 `/health` 与监控页自身端点。

为什么用纯 ASGI 而不是 BaseHTTPMiddleware：
  · 不缓冲响应流（媒体/音频仍可流式）；
  · 无论 handler 抛异常与否，`finally` 都能落一次记录（异常按 5xx 计）；
  · 开销 = 一次 perf_counter + 锁内自增，微秒级。

排除面（重要）：`/api/admin/system/*` 是监控页自己的 10 秒轮询。若计入，
一个管理员开着页面就能把“请求量/错误率”刷成假数据。
"""

from __future__ import annotations

import time

from backend.metrics import app_metrics

#: 监控页自身的端点前缀（不计入应用指标）
_EXCLUDED_PREFIXES = ("/api/admin/system/",)


class AppMetricsMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api/") or path.startswith(_EXCLUDED_PREFIXES):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            app_metrics.record(status_code, (time.perf_counter() - started) * 1000)
