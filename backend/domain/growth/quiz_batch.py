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
            # P3 性能族（2026-10-22 第一百一十五批）：`total_questions` 的 MAX **并进这条
            # GROUP BY**——原先在下面用字典推导逐书再查一次（N = 有测验记录的书数，上限 50）
            # → 书架页最坏多付 50 条 SQL。
            func.max(QuizAttempt.total_questions),
        )
        .filter(
            QuizAttempt.child_id == child.id,
            QuizAttempt.book_id.in_(book_ids),
            QuizAttempt.is_deleted == 0,
        )
        .group_by(QuizAttempt.book_id)
        .all()
    )
    total_map = {r[0]: (r[3] or 0) for r in attempt_rows}
    attempt_map = {r[0]: r for r in attempt_rows}
    out = []
    for bid in book_ids:
        used = attempt_map[bid][1] if bid in attempt_map else 0
        best = attempt_map[bid][2] if bid in attempt_map else 0
        total = total_map.get(bid, 0)
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
