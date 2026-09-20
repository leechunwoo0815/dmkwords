"""往期活动回顾（2026-09-20 C 批，客户需求「已结束的活动放到往期活动回顾里」）。

从 `service.py` 拆出（god-file 800 行上限；与 `admin_service.py` 同款"按用途拆子类"的做法）：
只管一件事——**往期列表的取数与参与统计**，复用 `ActivityService._activity_view`。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func

from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.activity.service import PAST_AFTER_HOURS, ActivityService


class PastActivityService(ActivityService):
    def list_past(self, *, limit: int = 30, offset: int = 0) -> list[dict]:
        """往期活动回顾。

        **按"开始已过 PAST_AFTER_HOURS 小时"取数，而不是 status=finished**：
        `activity_auto_finish` 只在"开始超 1 天且**无活跃报名**"时才置 finished——有人报名却
        没签到的活动过期后会永远停在 published，而 `list_upcoming` 又按时间把它过滤掉，
        于是这类活动**任何列表都不显示**（存量盲区）。按时间取数顺手覆盖它，
        也不依赖定时任务有没有跑过。取消的活动不进往期（整场作废，没有"回顾"可言）。

        返回**不含任何孩子信息**：只有活动自身字段 + 参与人数聚合（客户合规口径）。
        """
        """往期活动回顾（2026-09-20 C 批）。

        **按"开始已过 PAST_AFTER_HOURS 小时"取数，而不是 status=finished**：
        `activity_auto_finish` 只在"开始超 1 天且**无活跃报名**"时才置 finished——有人报名却
        没签到的活动过期后会永远停在 published，而 `list_upcoming` 又按时间把它过滤掉，
        于是这类活动**任何列表都不显示**（存量盲区）。按时间取数顺手覆盖它，
        也不依赖定时任务有没有跑过。取消的活动不进往期（整场作废，没有"回顾"可言）。
        """
        cutoff = datetime.now() - timedelta(hours=PAST_AFTER_HOURS)
        rows = (
            self.db.query(Activity)
            .filter(
                Activity.is_deleted == 0,
                Activity.status != Activity.STATUS_CANCELLED,
                Activity.start_at < cutoff,
            )
            .order_by(Activity.start_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        if not rows:
            return []
        ids = [a.id for a in rows]
        # 参与统计：一次聚合，禁 N+1（列表页最容易被写成每行一次 count）
        enrolled = dict(
            self.db.query(ActivityEnrollment.activity_id, func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id.in_(ids),
                ActivityEnrollment.is_deleted == 0,
                ActivityEnrollment.status != ActivityEnrollment.STATUS_CANCELLED,
            )
            .group_by(ActivityEnrollment.activity_id)
            .all()
        )
        checked = dict(
            self.db.query(ActivityEnrollment.activity_id, func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id.in_(ids),
                ActivityEnrollment.is_deleted == 0,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_CHECKED_IN,
            )
            .group_by(ActivityEnrollment.activity_id)
            .all()
        )
        out = []
        for a in rows:
            v = self._activity_view(a)  # 往期不报名 → 不带名额字段
            v["enrolled_total"] = enrolled.get(a.id, 0)
            v["checked_in_total"] = checked.get(a.id, 0)
            out.append(v)
        return out
