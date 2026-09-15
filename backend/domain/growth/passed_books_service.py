# backend/domain/growth/passed_books_service.py — 已通过测验的书（WM8 扩展，2026-09-15）
"""从 growth/service.py 拆出（god file 800 行限制，架构关门禁）。

用途：书架「已通过」页签——让孩子一眼看到「哪些书我已经测过了」，最直接的成就感来源。
判定口径与 QuizService.get_quiz 的 passed 一致：**词账有入账即为已通过**
（红线：通过才入账、一书一次、永不回收）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.file_utils import book_cover_url
from backend.domain.growth.models import QuizAttempt, WordsLedger


class PassedBooksService:
    """已通过清单：词账枚举 + 每本取最高分 attempt。"""

    def __init__(self, db: Session):
        self.db = db

    def list_for(self, child_id: int) -> list[dict]:
        from backend.domain.catalog.models import Book

        rows = (
            self.db.query(WordsLedger, Book)
            .join(Book, WordsLedger.book_id == Book.id)
            .filter(
                WordsLedger.child_id == child_id,
                WordsLedger.is_deleted == 0,
                Book.is_deleted == 0,
            )
            .order_by(WordsLedger.created_at.desc(), WordsLedger.id.desc())
            .all()
        )
        book_ids = [b.id for _w, b in rows]
        best: dict[int, QuizAttempt] = {}
        if book_ids:
            attempts = (
                self.db.query(QuizAttempt)
                .filter(
                    QuizAttempt.child_id == child_id,
                    QuizAttempt.book_id.in_(book_ids),
                    QuizAttempt.is_deleted == 0,
                )
                .all()
            )
            for a in attempts:
                cur = best.get(a.book_id)
                if cur is None or a.score > cur.score:
                    best[a.book_id] = a
        out: list[dict] = []
        for w, b in rows:
            a = best.get(b.id)
            total = int(a.total_questions) if a else 0
            score = int(a.score) if a else 0
            out.append(
                {
                    "book_id": b.id,
                    "title": b.title,
                    "author": b.author,
                    "word_count": b.word_count,
                    "cover_url": book_cover_url(b.id, b.cover_path),
                    "score": score,
                    "total": total,
                    # total==0（词账已入账但测验记录缺失）→ 前端显示「成绩待同步」，不编 0 分
                    "best_percent": round(score * 100 / total) if total else 0,
                    "passed_at": str(w.created_at),
                }
            )
        return out
