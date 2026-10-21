# backend/common/ttl_cache.py — 进程内 TTL 缓存（P2-40）
"""单机部署（ADR-005）下的**进程内** TTL 缓存：与配置中心 60s 缓存同款纪律。

**适用判据**（不是"看到查询就加缓存"）：同时满足三条才用——
① 结果**随数据量线性增长**的聚合（全馆统计、跨表 SUM/COUNT）；
② 处在**高频请求路径**上（每个请求都重算）；
③ **允许秒级陈旧**（展示型统计，不是资金/库存口径）。

反例（禁加缓存）：资金余额、库存名额、待办计数这类"算出来就是要立刻对"的口径——
缓存它们等于制造"看起来对"的假绿（本项目已有 8 例计数不同源前科）。

多副本部署时进程内缓存会各自为政（ADR-005 明确单机，故可接受）；真要多副本，
这里换 Redis 即可（接口不变）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class TtlCache:
    """`get_or_compute(key, factory)`：命中且未过期直接返回，否则算完缓存。"""

    def __init__(self, ttl_seconds: float, name: str = ""):
        self.ttl_seconds = ttl_seconds
        self.name = name
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float]] = {}

    def get_or_compute(self, key: str, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and hit[1] > now:
                return hit[0]
        # 锁外计算：慢聚合不阻塞其它 key（并发未命中时可能重复算一次，代价可接受）
        value = factory()
        with self._lock:
            self._store[key] = (value, time.monotonic() + self.ttl_seconds)
        return value

    def clear(self, key: str | None = None) -> None:
        """清缓存（测试夹具与"数据变更后立即生效"的调用方使用）。"""
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)
