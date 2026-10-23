# backend/domain/growth/growth_handlers.py — growth 域事件订阅（main.py 启动注册）
"""reading.checkin → 打卡周期积分（同事务，失败回滚）。"""

from __future__ import annotations

import logging

from backend.common.events import CheckInEvent, event_bus

logger = logging.getLogger(__name__)


def _handle_checkin(event: CheckInEvent, db) -> None:
    if db is None:
        # B8（第九轮审查 P3 族）：打卡周期奖是**会丢的积分**——缺 db 时原先只 warning，
        # 静默丢奖在日志里不刺眼。这里是周期日双发积分的唯一入口，必须响亮。
        logger.error(
            "reading.checkin 事件缺少 db session——打卡周期积分**未发放**（child_id=%s）",
            event.child_id,
        )
        return
    from backend.domain.growth.service import GrowthService

    GrowthService(db).on_checkin(event.child_id, event.streak_days)


def register_growth_handlers() -> None:
    event_bus.subscribe("reading.checkin", _handle_checkin)
