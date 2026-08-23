# backend/domain/growth/board_service.py — 五榜单 + 阅读护照（WM8）
"""隐私口径（R-317/R-318）：英文名/昵称展示；无手机号无全名；周期榜仅有效会员。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import ValidationError
from backend.domain.growth.models import WordsLedger
from backend.domain.growth.service import GrowthService
from backend.domain.identity.models import Child


class LeaderboardService:
    """五榜单（R-317/R-318）：周/月/年/总/进步；隐私口径（英文名/昵称，无手机号全名）。"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _week_start(d: date) -> date:
        """自然周（周一）。"""
        return d - timedelta(days=d.weekday())

    @staticmethod
    def _display_name(child: Child) -> str:
        return child.english_name or f"小朋友{child.id:03d}"

    def _entries(
        self, start: datetime | None, active_only: bool, end: datetime | None = None
    ) -> list[dict]:
        q = (
            self.db.query(Child, func.coalesce(func.sum(WordsLedger.word_count), 0))
            .outerjoin(
                WordsLedger,
                (WordsLedger.child_id == Child.id) & (WordsLedger.is_deleted == 0),
            )
            .filter(Child.is_deleted == 0)
        )
        if start is not None:
            q = q.filter(WordsLedger.created_at >= start)
        if end is not None:
            q = q.filter(WordsLedger.created_at < end)
        if active_only:
            # 周期榜仅有效会员（R-317）；正式会员到期即剔除（D1：不依赖定时任务落库）
            today = datetime.now().date()
            q = q.filter(
                or_(
                    Child.member_status.in_(
                        [Child.MEMBER_OBSERVATION, Child.MEMBER_PENDING_EVALUATION]
                    ),
                    and_(
                        Child.member_status == Child.MEMBER_FORMAL,
                        Child.member_expire >= today,
                    ),
                )
            )
        rows = q.group_by(Child.id).all()
        entries = [
            {
                "child_id": child.id,
                "name": self._display_name(child),
                "avatar": child.avatar,
                "words": int(words),
                "member_status": child.member_status,
                "is_history": child.is_expired_member
                or child.member_status == Child.MEMBER_WITHDRAWN,
            }
            for child, words in rows
        ]
        entries.sort(key=lambda e: e["words"], reverse=True)
        return entries

    def board(self, viewer: Child, period: str) -> dict:
        if period not in ("week", "month", "year", "total", "progress"):
            raise ValidationError("榜单类型不正确")
        today = datetime.now().date()
        min_increment = int(ConfigService(self.db).get_value("progress_min_increment"))

        if period == "week":
            start = datetime.combine(self._week_start(today), datetime.min.time())
            entries = [e for e in self._entries(start, active_only=True) if e["words"] > 0]
            title = "本周词数榜"
        elif period == "month":
            start = datetime.combine(today.replace(day=1), datetime.min.time())
            entries = [e for e in self._entries(start, active_only=True) if e["words"] > 0]
            title = "本月词数榜"
        elif period == "year":
            start = datetime.combine(today.replace(month=1, day=1), datetime.min.time())
            entries = [e for e in self._entries(start, active_only=True) if e["words"] > 0]
            title = "年度词数榜"
        elif period == "total":
            entries = [e for e in self._entries(None, active_only=False) if e["words"] > 0]
            title = "总榜（历史荣誉）"
        else:  # progress
            this_start = datetime.combine(self._week_start(today), datetime.min.time())
            last_start = this_start - timedelta(days=7)
            this_week = {
                e["child_id"]: e["words"] for e in self._entries(this_start, active_only=True)
            }
            last_week = {
                e["child_id"]: e["words"]
                for e in self._entries(last_start, active_only=True, end=this_start)
            }
            entries = []
            for cid, cur in this_week.items():
                delta = cur - last_week.get(cid, 0)
                if delta >= min_increment:
                    child = self.db.query(Child).filter(Child.id == cid).first()
                    if child:
                        entries.append(
                            {
                                "child_id": cid,
                                "name": self._display_name(child),
                                "avatar": child.avatar,
                                "words": delta,
                                "member_status": child.member_status,
                                "is_history": False,
                            }
                        )
            entries.sort(key=lambda e: e["words"], reverse=True)
            title = "进步榜（本周比上周多读）"

        my_rank = None
        for i, e in enumerate(entries):
            if e["child_id"] == viewer.id:
                my_rank = i + 1
                e["is_me"] = True
        return {
            "period": period,
            "title": title,
            "entries": entries[:50],
            "my_rank": my_rank,
            "progress_min_increment": min_increment if period == "progress" else None,
        }


class PassportService:
    """阅读护照（FEAT-052：词数/等级/勋章/里程碑/最近读完；退会只读）。"""

    def __init__(self, db: Session):
        self.db = db

    def passport(self, child: Child) -> dict:
        summary = GrowthService(self.db).summary(child)
        recent = GrowthService(self.db).words_list(child.id, limit=10)
        from backend.domain.reading.models import CheckIn

        checkins = (
            self.db.query(func.count(CheckIn.id))
            .filter(CheckIn.child_id == child.id, CheckIn.is_deleted == 0)
            .scalar()
        )
        return {
            **summary,
            "child_name": child.name,
            "english_name": child.english_name,
            "avatar": child.avatar,
            "total_checkin_days": int(checkins or 0),
            "read_only": child.member_status == Child.MEMBER_WITHDRAWN or child.is_expired_member,
            "recent_books": recent,
        }
