# backend/domain/growth/growth_handlers.py — growth 域事件订阅（main.py 启动注册）
"""reading.checkin → 打卡周期积分（同事务，失败回滚）。"""

from __future__ import annotations

import logging

from backend.common.events import CheckInEvent, event_bus

logger = logging.getLogger(__name__)


def _handle_checkin(event: CheckInEvent, db) -> None:
    if db is None:
        logger.warning("reading.checkin 事件缺少 db session，跳过")
        return
    from backend.domain.growth.service import GrowthService

    GrowthService(db).on_checkin(event.child_id, event.streak_days)


def register_growth_handlers() -> None:
    event_bus.subscribe("reading.checkin", _handle_checkin)
