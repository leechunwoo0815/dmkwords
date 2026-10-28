# backend/domain/growth/quiz_queries.py — 测验成绩的三条共享查询（FE-2，2026-10-28）
"""状态页与提交响应共用的成绩查询，避免两处口径漂移（也把 `service.py` 拉回 800 行内）。

三条口径：
  · `best_score`：该书历史最高「答对题数」（提交响应里的 best_score 用）；
  · `best_attempt`：正确率最高的一次尝试（同分取题量更小、再取更早——与状态页历史行为一致）；
  · `points_added_for_book`：该书累计入账积分（词数转化 + 首过 + 满分三类）。
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.domain.growth.models import PointLedger, QuizAttempt

#: 该书积分口径的三个 reason_type（词数转化 / 小挑战首次通过 / 小挑战满分）
BOOK_POINT_REASONS = ("words_convert", "quiz_first_pass", "quiz_full_marks")


def best_score(db: Session, child_id: int, book_id: int) -> int:
    value = (
        db.query(func.max(QuizAttempt.score))
        .filter(
            QuizAttempt.child_id == child_id,
            QuizAttempt.book_id == book_id,
            QuizAttempt.is_deleted == 0,
        )
        .scalar()
    )
    return int(value or 0)


def best_attempt(db: Session, child_id: int, book_id: int) -> QuizAttempt | None:
    return (
        db.query(QuizAttempt)
        .filter(
            QuizAttempt.child_id == child_id,
            QuizAttempt.book_id == book_id,
            QuizAttempt.is_deleted == 0,
        )
        .order_by(QuizAttempt.score.desc(), QuizAttempt.total_questions.asc())
        .first()
    )


def points_added_for_book(db: Session, child_id: int, book_id: int) -> int:
    value = (
        db.query(func.sum(PointLedger.points))
        .filter(
            PointLedger.child_id == child_id,
            PointLedger.related_id == book_id,
            PointLedger.reason_type.in_(BOOK_POINT_REASONS),
            PointLedger.is_deleted == 0,
        )
        .scalar()
    )
    return int(value or 0)
