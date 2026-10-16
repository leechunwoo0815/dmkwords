# backend/domain/growth/checkin_awards.py — 打卡周期奖（7/30 天独立周期，防重复发）
"""为什么单开（与 `reading/reservation_ops.py` / `growth/quiz_events.py` 同一处置）：
`growth/service.py` 触及架构关 god-file 上限（>800 行）；本函数是一段自洽的发奖逻辑
（幂等键 + 周期序号 + 积分入账），与"汇总视图/报告"等无关，独立出来也让幂等口径更醒目。

**幂等键（P2 #20，2026-10-16 第三轮大审查）** = `(孩子, 周期类型, 达成当天, 达成时连续天数)`：

- 原口径 `cycle_no = streak // N` + 按 cycle_no 查重 —— 断签后 streak 从 0 重数，下一个 7 天周期
  又是 `cycle_no=1` → 撞历史记录 → **此后终身不再发周期奖**（真实缺陷）。
- 不能只拿 `streak_at` 当键：断签重数后 streak 值会**重复**（又到 7），会把新周期误判成同一次达成。
- 不能只拿"当天"当键：同一天不同 streak 值（测试压缩时间 / 直接调用）是**不同周期**，应当各发一次
  （`features/points.feature` 的「第 7 天 +10 → 第 14 天再 +10」正是这条口径）。
- 所以用**两者合取**：同一天 + 同一 streak 值 = 同一次达成（幂等）；其余情况都是新周期。
  生产里打卡每天只 +1，故"同一天同一 streak"只会来自重复调用。

周期序号 `cycle_no` **只增不复用**（取已有条数 +1）：唯一索引 `uq_streak_award` 是
`(child_id, cycle_type, cycle_no)`，不复用即不撞。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.domain.growth.models import CheckinStreakRecord, ChildGrowthState

#: (周期天数, 配置键, 积分流水类型)
_CYCLES = (
    (7, "checkin_7days_bonus", "checkin_7"),
    (30, "checkin_30days_bonus", "checkin_30"),
)


def award_checkin_cycles(
    db: Session, state: ChildGrowthState, child_id: int, streak: int, *, add_points
) -> list[dict]:
    """按连续天数发周期奖，返回本次发出的 `[{type, points}]`。

    `add_points`：宿主服务注入的积分入账口（`GrowthService._add_points`）——本模块不复制入账口径。
    """
    awarded: list[dict] = []
    today = datetime.now().date()
    for cycle_days, config_key, rtype in _CYCLES:
        if streak <= 0 or streak % cycle_days != 0:
            continue
        cycle_type = f"days{cycle_days}"
        dup = (
            db.query(func.count(CheckinStreakRecord.id))
            .filter(
                CheckinStreakRecord.child_id == child_id,
                CheckinStreakRecord.cycle_type == cycle_type,
                CheckinStreakRecord.streak_at == streak,
                func.date(CheckinStreakRecord.awarded_at) == today,
                CheckinStreakRecord.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            continue
        cycle_no = (
            db.query(func.count(CheckinStreakRecord.id))
            .filter(
                CheckinStreakRecord.child_id == child_id,
                CheckinStreakRecord.cycle_type == cycle_type,
                CheckinStreakRecord.is_deleted == 0,
            )
            .scalar()
            or 0
        ) + 1
        bonus = int(ConfigService(db).get_value(config_key))
        if bonus <= 0:
            continue
        add_points(
            state,
            bonus,
            rtype,
            f"连续打卡 {streak} 天（第 {cycle_no} 个 {cycle_days} 天周期）",
        )
        db.add(
            CheckinStreakRecord(
                child_id=child_id,
                cycle_type=cycle_type,
                cycle_no=cycle_no,
                streak_at=streak,
            )
        )
        # 立刻 flush：本会话 autoflush 是关的，不然同事务内的下一次查重看不见这条（幂等键依赖它能被查到）
        db.flush()
        awarded.append({"type": rtype, "points": bonus})
    return awarded
