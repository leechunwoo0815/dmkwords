# backend/common/async_utils.py — 同步代码调异步网关的唯一样式
"""为什么有它：项目里所有外部网关（短信/微信支付）都是 `async`，而 Service 与 Router
一律**同步**（同步端点跑在线程池，不会阻塞事件循环——这正是审查 P1-2 的教训）。

所以必须有一座桥：`run_coro()`。它只能在**同步上下文**调用：`asyncio.run` 在有运行中
事件循环的线程里会直接报错——这是刻意的（宁可炸也不要静默死锁）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


def run_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """把协程跑到底并返回结果（每个调用独立事件循环）。"""
    return asyncio.run(coro)
