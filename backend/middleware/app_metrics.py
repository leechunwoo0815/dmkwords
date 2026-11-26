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
        # None = 响应头还没发出（客户端提前断开/取消）。这种"没答完"不是服务端 5xx，
        # 记 5xx 会让错误率虚增；handler 真抛异常时 FastAPI 仍会以 500 起响应，
        # 异常在 start 之前抛出 → 由异常处理器的响应补上真实状态码。
        status_code: int | None = None

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 没有响应头就抛异常 → 按 500 计（服务端错误），再原样抛出
            status_code = 500
            raise
        finally:
            if status_code is not None:
                app_metrics.record(status_code, (time.perf_counter() - started) * 1000)
