# backend/domain/growth/quiz_events.py — 测验结果 → 领域事件发布（单一入口）
"""为什么单开（与 `reading/reservation_ops.py` / `circulation/reminders.py` 同一处置）：
`growth/service.py` 触及架构关 god-file 上限（>800 行），把"提交结果怎么变成事件"这段
自洽逻辑分出来；顺带把**通知去重键的来源**（提交记录 id）写在一处，避免再有人漏传。

P1 #2（2026-10-15 大审查）：`QuizAttempt.id` 就是去重键——PRD §十 要求「测验成绩（**每次提交后**）」，
而通知默认键 `"1"` 是"终身一条"：同一孩子第 2 次提交起会被唯一索引静默吞掉（第一次"未通过"之后，
后来的"通过"也永远发不出）。所以这里**强制要求** `submission_id`。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.common.events import QuizFailedEvent, QuizPassedEvent, event_bus


def publish_quiz_result(
    db: Session,
    *,
    child: Any,
    book: Any,
    passed: bool,
    score: int,
    submission_id: int,
) -> None:
    """提交一次测验 → 发"通过/未通过"事件（订阅器据此发家长通知，去重键=提交记录 id）。"""
    if passed:
        event_bus.publish(
            QuizPassedEvent(
                child_id=child.id,
                book_id=book.id,
                quiz_id=book.id,
                word_count=book.word_count,
                submission_id=submission_id,
            ),
            db=db,
        )
        return
    event_bus.publish(
        QuizFailedEvent(
            child_id=child.id,
            book_id=book.id,
            quiz_id=book.id,
            score=score,
            submission_id=submission_id,
        ),
        db=db,
    )
