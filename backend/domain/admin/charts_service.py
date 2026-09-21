# backend/domain/admin/charts_service.py — 仪表盘图形区数据（2026-09-21）
"""用户需求：「仪表盘不仅仅是数字，还有很多的图，很酷炫的图，以后要投到店外的电视机上」，
并裁定「就用仪表盘页面，不要再新增页」——因此本服务只服务**既有仪表盘页**的图形区。

为什么单独一个文件：`admin/service.py` 已 775 行、贴到架构关 800 行上限（god-file 规则）；
图形区是**只读聚合**，与后台账号/配置/审计的写路径无关，切开最省事（与 circulation 域
拆 `records_service` / `scan_service` 同族）。

三个聚合都走"一次查询 + Python 侧补零/映射"，避免 N+1：
  ① 近 N 天借出/归还趋势（缺的日期补 0，前端直接画折线不需要再补）
  ② 近 30 天热门书 TOP N（含书名，封面由前端按 book_id 拼既有 cover-media 端点）
  ③ 会员构成（按 member_status 分组）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.domain.catalog.models import Book
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child

_STATUS_ORDER = ("none", "observation", "pending_evaluation", "formal", "expired", "withdrawn")


class DashboardChartsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def charts(self, *, days: int = 14, top: int = 5) -> dict:
        days = max(3, min(days, 60))
        top = max(1, min(top, 10))
        today = date.today()
        start_day = today - timedelta(days=days - 1)
        since = datetime.combine(start_day, datetime.min.time())

        borrowed = dict(
            self.db.query(func.date(BorrowRecord.borrowed_at), func.count(BorrowRecord.id))
            .filter(BorrowRecord.is_deleted == 0, BorrowRecord.borrowed_at >= since)
            .group_by(func.date(BorrowRecord.borrowed_at))
            .all()
        )
        returned = dict(
            self.db.query(func.date(BorrowRecord.returned_at), func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.is_deleted == 0,
                BorrowRecord.returned_at.isnot(None),
                BorrowRecord.returned_at >= since,
            )
            .group_by(func.date(BorrowRecord.returned_at))
            .all()
        )
        trend = []
        for i in range(days):
            d = start_day + timedelta(days=i)
            trend.append(
                {
                    "date": d.strftime("%m-%d"),
                    "borrowed": int(borrowed.get(d, 0)),
                    "returned": int(returned.get(d, 0)),
                }
            )

        hot_rows = (
            self.db.query(BorrowRecord.book_id, func.count(BorrowRecord.id).label("c"))
            .filter(
                BorrowRecord.is_deleted == 0,
                BorrowRecord.borrowed_at >= datetime.now() - timedelta(days=30),
            )
            .group_by(BorrowRecord.book_id)
            .order_by(func.count(BorrowRecord.id).desc(), BorrowRecord.book_id.asc())
            .limit(top)
            .all()
        )
        titles = (
            dict(
                self.db.query(Book.id, Book.title)
                .filter(Book.id.in_([r[0] for r in hot_rows]))
                .all()
            )
            if hot_rows
            else {}
        )
        hot_books = [
            {"book_id": int(bid), "title": titles.get(bid, ""), "borrow_count": int(cnt)}
            for bid, cnt in hot_rows
        ]

        rows = (
            self.db.query(Child.member_status, func.count(Child.id))
            .filter(Child.is_deleted == 0)
            .group_by(Child.member_status)
            .all()
        )
        counts = {str(s): int(c) for s, c in rows}
        member_status = [
            {"status": s, "count": counts.get(s, 0)} for s in _STATUS_ORDER if counts.get(s, 0) > 0
        ]

        return {"borrow_trend": trend, "hot_books": hot_books, "member_status": member_status}
