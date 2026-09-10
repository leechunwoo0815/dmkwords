# backend/domain/reading_circle/snapshot_service.py — 周榜快照（WM14-B）
"""每个自然周结算**上一个完整自然周**的周榜名次 → circle_rank_snapshots。

消费方：上榜卡（rank≤10）、上升卡（与上周快照对比名次）。
口径纪律（计数同源）：名次/词数**直接复用 LeaderboardService.period_entries**
（周榜同款 active_only 口径）——禁在本文件自创第二套聚合（计数同源已有 7 案前科）。
幂等：唯一索引 (child_id, week_start)，且整周已存在即整体跳过
（scheduler 是 interval 触发器，靠本幂等实现"每周一次"语义）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from backend.domain.growth.board_service import LeaderboardService
from backend.domain.reading_circle.models import CircleRankSnapshot


class CircleSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def last_complete_week(today: date | None = None) -> tuple[date, date]:
        """上一个完整自然周 [上周一, 本周一)——与 ReportService 周报区间同源。"""
        today = today or datetime.now().date()
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday

    def run_weekly_snapshot(self, today: date | None = None) -> int:
        """结算上一完整周名次；返回本次落库条数（已结算过返回 0）。

        today 可注入：演示/测试要结算历史周时传该周内的任意一天（seed 用）。
        """
        start_d, end_d = self.last_complete_week(today)
        start = datetime.combine(start_d, datetime.min.time())
        end = datetime.combine(end_d, datetime.min.time())

        exists = (
            self.db.query(CircleRankSnapshot.id)
            .filter(
                CircleRankSnapshot.week_start == start_d,
                CircleRankSnapshot.is_deleted == 0,
            )
            .first()
        )
        if exists:
            return 0  # 本周已结算（interval 任务每小时自检，此处兜住"每周一次"）

        entries = LeaderboardService(self.db).period_entries(start, end)
        for i, e in enumerate(entries):
            self.db.add(
                CircleRankSnapshot(
                    child_id=e["child_id"],
                    week_start=start_d,
                    rank=i + 1,
                    words=e["words"],
                )
            )
        self.db.commit()
        return len(entries)
