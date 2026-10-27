# tests/unit/activity_time.py — 测试用活动时间挪动（签到窗判据）
"""产品口径 docs/23 §二：签到窗 = 开始前 60 分钟 ~ 结束后 30 分钟。

多数历史用例先建"未来 3 天"的活动再立即签到（当时没有时间窗）。这些用例要验的是
签到之后的退款/结算分支，不是窗口本身，因此统一在签到前把活动挪进窗口；
窗口本身的早到/晚到判据由 `test_product_decisions_20261027.py` 单独覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def shift_activity_to_now(
    activity_id: int, *, start_minutes: int = -10, duration_minutes: int = 120
) -> None:
    from backend.database import get_session
    from backend.domain.activity.models import Activity

    now = datetime.now()
    with get_session() as db:
        db.query(Activity).filter(Activity.id == activity_id).update(
            {
                "start_at": now + timedelta(minutes=start_minutes),
                "end_at": now + timedelta(minutes=duration_minutes),
            }
        )
        db.commit()


def shift_enrollment_activity_to_now(enrollment_id: int) -> None:
    """按报名 id 找活动并挪进窗口（测试辅助函数只有报名 id 时用）。"""
    from backend.database import get_session
    from backend.domain.activity.models import ActivityEnrollment

    with get_session() as db:
        activity_id = (
            db.query(ActivityEnrollment.activity_id)
            .filter(ActivityEnrollment.id == enrollment_id)
            .scalar()
        )
    assert activity_id is not None, f"报名不存在：{enrollment_id}"
    shift_activity_to_now(activity_id)
