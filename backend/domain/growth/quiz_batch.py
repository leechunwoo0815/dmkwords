# backend/domain/growth/quiz_batch.py — 书架角标批量状态（T43/U2 独立模块）
"""从 GrowthService 拆出（god file 800 行限）；口径与 get_quiz 严格一致。"""

from __future__ import annotations

from backend.common.config_service import ConfigService
from backend.common.exceptions import ValidationError
from backend.domain.growth.models import QuizAttempt, WordsLedger
from backend.domain.growth.quiz_stats import best_attempt_stats


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
    # R11-c2/G3：改成"逐次尝试"取最佳（分子分母必须来自同一次）。
    # 行数上限 = 50 书 × 每书尝试数（受 quiz_max_attempts 约束），仍是一条 SQL。
    attempt_rows = (
        db.query(
            QuizAttempt.book_id,
            QuizAttempt.score,
            QuizAttempt.total_questions,
            QuizAttempt.id,
        )
        .filter(
            QuizAttempt.child_id == child.id,
            QuizAttempt.book_id.in_(book_ids),
            QuizAttempt.is_deleted == 0,
        )
        .all()
    )
    grouped: dict[int, list[tuple]] = {}
    for book_id, score, total, attempt_id in attempt_rows:
        grouped.setdefault(book_id, []).append((score, total, attempt_id))
    stats = {bid: best_attempt_stats(rows) for bid, rows in grouped.items()}
    out = []
    for bid in book_ids:
        used = len(grouped.get(bid, []))
        best, total = stats.get(bid) or (0, 0)
        # P2-15（2026-10-17 第六十九批·大审查）：解锁判据与 `get_quiz` 严格一致——
        # **有词账即已解锁**（通过测验的前提就是听完；词账/测验记录可能早于或独立于
        # ReadingProgress 存在，如榜单/里程碑造数只落词账）。此前先判 `finished` → 那类书
        # 在书架角标显示 `locked`，而同一孩子的「已通过」页签与详情页却是 PASSED（同屏自相矛盾）。
        if bid not in finished_ids and bid not in passed_ids:
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
