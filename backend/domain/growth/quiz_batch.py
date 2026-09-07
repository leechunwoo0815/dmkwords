# backend/domain/growth/quiz_batch.py — 书架角标批量状态（T43/U2 独立模块）
"""从 GrowthService 拆出（god file 800 行限）；口径与 get_quiz 严格一致。"""

from __future__ import annotations

from sqlalchemy import func

from backend.common.config_service import ConfigService
from backend.common.exceptions import ValidationError
from backend.domain.growth.models import QuizAttempt, WordsLedger


def quiz_status_batch(db, child, book_ids: list[int]) -> list[dict]:
    """T43（U2）：书架角标批量接口——3 次 IN 查询返回全量状态（禁 N+1）。

    口径与 get_quiz 严格一致：locked（未完播）/passed（词账有记录）/
    failed（次数用尽）/available；best_percent 顺带供详情页复用。
    """
    from backend.domain.reading.models import ReadingProgress

    if not book_ids or len(book_ids) > 50:
        raise ValidationError("book_ids 非法（1-50 个）")
    max_attempts = int(ConfigService(db).get_value("quiz_max_attempts"))
    finished_ids = {
        r[0]
        for r in db.query(ReadingProgress.book_id)
        .filter(
            ReadingProgress.child_id == child.id,
            ReadingProgress.book_id.in_(book_ids),
            ReadingProgress.finished == 1,
            ReadingProgress.is_deleted == 0,
        )
        .all()
    }
    passed_ids = set(
        r[0]
        for r in db.query(WordsLedger.book_id)
        .filter(
            WordsLedger.child_id == child.id,
            WordsLedger.book_id.in_(book_ids),
            WordsLedger.is_deleted == 0,
        )
        .all()
    )
    attempt_rows = (
        db.query(
            QuizAttempt.book_id,
            func.count(QuizAttempt.id),
            func.max(QuizAttempt.score),
        )
        .filter(
            QuizAttempt.child_id == child.id,
            QuizAttempt.book_id.in_(book_ids),
            QuizAttempt.is_deleted == 0,
        )
        .group_by(QuizAttempt.book_id)
        .all()
    )
    total_map = {
        bid: (
            db.query(func.max(QuizAttempt.total_questions))
            .filter(
                QuizAttempt.child_id == child.id,
                QuizAttempt.book_id == bid,
                QuizAttempt.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        for bid, _, _ in attempt_rows
    }
    attempt_map = {r[0]: r for r in attempt_rows}
    out = []
    for bid in book_ids:
        used = attempt_map[bid][1] if bid in attempt_map else 0
        best = attempt_map[bid][2] if bid in attempt_map else 0
        total = total_map.get(bid, 0)
        if bid not in finished_ids:
            status = "locked"
        elif bid in passed_ids:
            status = "passed"
        elif used >= max_attempts:
            status = "failed"
        else:
            status = "available"
        out.append(
            {
                "book_id": bid,
                "status": status,
                "best_percent": round(best * 100 / total) if total else 0,
            }
        )
    return out
