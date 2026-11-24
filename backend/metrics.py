# backend/metrics.py — 进程内应用指标注册表（管理端服务器监控用）
"""轻量应用指标：请求数 / 4xx / 5xx / P95 延迟。

为什么是进程内（不是 Prometheus）：
  单店单进程部署（systemd + uvicorn），引入 Prometheus + exporter 会多占 100–200MB 内存，
  对 2GB 小机不划算。本模块只做固定桶计数，采样任务每分钟快照一次写 `system_metrics`，
  历史由数据库承载；内存占用与请求量无关（固定 10 个桶）。

口径：
  - 只统计 `/api/*`（中间件过滤，见 `middleware/app_metrics.py`）；
  - **排除** `/health` 与 `/api/admin/system/*`（监控页自身的 10 秒轮询不能污染请求量）；
  - P95 由固定桶累计估算（5/10/25/…/5000ms），不是精确分位数——趋势够用，成本固定。
"""

from __future__ import annotations

import threading
import time

#: P95 桶上界（毫秒）；最后一个隐式桶 = 大于 5000ms
LATENCY_BUCKETS_MS: tuple[int, ...] = (5, 10, 25, 50, 100, 200, 500, 1000, 2000, 5000)


class AppMetricsRegistry:
    """线程安全的分钟窗口计数器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._window_started = time.monotonic()
        self._requests = 0
        self._errors_4xx = 0
        self._errors_5xx = 0
        self._buckets = [0] * (len(LATENCY_BUCKETS_MS) + 1)

    def record(self, status_code: int, duration_ms: float) -> None:
        """记录一次请求。中间件在 finally 里调用，异常也会落成 5xx。"""
        with self._lock:
            self._requests += 1
            if 400 <= status_code < 500:
                self._errors_4xx += 1
            elif status_code >= 500:
                self._errors_5xx += 1
            for idx, upper in enumerate(LATENCY_BUCKETS_MS):
                if duration_ms <= upper:
                    self._buckets[idx] += 1
                    break
            else:
                self._buckets[-1] += 1

    def snapshot(self) -> dict:
        """读取当前窗口（不重置）；live 接口用。"""
        with self._lock:
            return self._snapshot_locked()

    def snapshot_and_rotate(self) -> dict:
        """读取并重置窗口；60 秒采样任务用。"""
        with self._lock:
            data = self._snapshot_locked()
            self._reset_locked()
            return data

    def reset(self) -> None:
        """测试用：清空窗口。"""
        with self._lock:
            self._reset_locked()

    def _snapshot_locked(self) -> dict:
        return {
            "requests": self._requests,
            "errors_4xx": self._errors_4xx,
            "errors_5xx": self._errors_5xx,
            "p95_ms": self._p95_locked(),
            "window_seconds": round(time.monotonic() - self._window_started, 1),
        }

    def _p95_locked(self) -> int:
        if self._requests <= 0:
            return 0
        threshold = self._requests * 0.95
        cumulative = 0
        for idx, upper in enumerate(LATENCY_BUCKETS_MS):
            cumulative += self._buckets[idx]
            if cumulative >= threshold:
                return upper
        return LATENCY_BUCKETS_MS[-1]

    def _reset_locked(self) -> None:
        self._requests = 0
        self._errors_4xx = 0
        self._errors_5xx = 0
        self._buckets = [0] * (len(LATENCY_BUCKETS_MS) + 1)
        self._window_started = time.monotonic()


#: 全进程单例（中间件写、采样任务/ live 接口读）
app_metrics = AppMetricsRegistry()
