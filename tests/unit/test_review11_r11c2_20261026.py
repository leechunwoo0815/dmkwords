# tests/unit/test_review11_r11c2_20261026.py — 第十一轮整改 R11-c2（第一百五十三批）
"""R11-c2 三条的机器判据：B5 突破卡新高 / G3 最佳正确率三处同口径 / G4 全表加载与 N+1。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from backend.common.exceptions import ValidationError
from backend.database import engine
from backend.domain.admin.models import AdminUser
from backend.domain.catalog.models import Book
from backend.domain.growth.models import (
    ChildGrowthState,
    MilestoneAward,
    QuizAttempt,
    WordsLedger,
)
from backend.domain.growth.passed_books_service import PassedBooksService
from backend.domain.growth.quiz_batch import quiz_status_batch
from backend.domain.growth.service import GrowthService
from backend.domain.identity.models import Child, Parent
from backend.domain.reading_circle.card_engine import assemble_card_data
from backend.domain.reading_circle.models import CirclePost


def _child(db, phone="13982026401"):
    parent = Parent(name="R11C2家长", phone=phone)
    db.add(parent)
    db.flush()
    child = Child(parent_id=parent.id, name="R11C2孩")
    db.add(child)
    db.flush()
    return child


def _book(db, title="R11C2书"):
    book = Book(title=title, word_count=500)
    db.add(book)
    db.flush()
    return book


# ---------------- B5 ----------------


def test_breakthrough_requires_true_new_high(db):
    """RED（只校验门槛）：任意达标日都能拿到文案恒印"单日新高"的卡。"""
    child = _child(db)
    # `uq_words_child_book` 终身唯一 → 两个不同日要用两本书（日汇总跨书求和）
    book_a = _book(db, "R11C2书A")
    book_b = _book(db, "R11C2书B")
    today = datetime.now().date()
    best_day = today - timedelta(days=1)
    low_day = today - timedelta(days=2)
    db.add_all(
        [
            WordsLedger(
                child_id=child.id,
                book_id=book_a.id,
                word_count=1500,
                created_at=datetime.combine(best_day, datetime.min.time()) + timedelta(hours=9),
            ),
            WordsLedger(
                child_id=child.id,
                book_id=book_b.id,
                word_count=1200,
                created_at=datetime.combine(low_day, datetime.min.time()) + timedelta(hours=9),
            ),
        ]
    )
    db.commit()

    # 最高的一天可以晒
    data = assemble_card_data(
        db, child, CirclePost.CARD_BREAKTHROUGH, int(best_day.strftime("%Y%m%d"))
    )
    assert data["value_text"] == "1,500 词"
    # 更低的一天：不是新高
    with pytest.raises(ValidationError):
        assemble_card_data(db, child, CirclePost.CARD_BREAKTHROUGH, int(low_day.strftime("%Y%m%d")))


# ---------------- G3 ----------------


def test_three_quiz_consumers_agree_on_best_accuracy(db):
    """RED（MAX(score)/MAX(total) 分开取）：同一本书三处百分率不同。"""
    child = _child(db, phone="13982026402")
    book = _book(db)
    db.add(
        WordsLedger(
            child_id=child.id,
            book_id=book.id,
            word_count=500,
            created_at=datetime.now() - timedelta(days=1),
        )
    )
    db.add_all(
        [
            QuizAttempt(
                child_id=child.id,
                book_id=book.id,
                score=3,
                total_questions=5,  # 60%（最佳）
                submitted_at=datetime.now() - timedelta(days=2),
            ),
            QuizAttempt(
                child_id=child.id,
                book_id=book.id,
                score=4,
                total_questions=8,  # 50%
                submitted_at=datetime.now() - timedelta(days=1),
            ),
        ]
    )
    db.commit()

    badge = quiz_status_batch(db, child, [book.id])[0]
    assert badge["best_percent"] == 60, (
        f"书架角标应取 3/5=60%（RED=MAX(score)=4 + MAX(total)=8 → 50%）实 {badge['best_percent']}"
    )

    overview = next(r for r in GrowthService(db).quiz_overview(child.id) if r["book_id"] == book.id)
    assert (overview["best_score"], overview["total_questions"]) == (3, 5)

    passed = next(r for r in PassedBooksService(db).list_for(child.id) if r["book_id"] == book.id)
    assert (passed["score"], passed["total"]) == (3, 5)


# ---------------- G4 ----------------


def test_recalc_levels_batches_milestone_queries(db):
    """RED（逐孩子查已授节点）：N 个孩子 = N 条 milestone_awards 查询。"""
    admin = db.query(AdminUser).first()
    children = [_child(db, phone=f"1398202641{i}") for i in range(5)]
    for c in children:
        db.add(ChildGrowthState(child_id=c.id, words_total=100000, books_total=10))
    db.commit()

    stmts: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _executemany):
        if "FROM milestone_awards" in statement:
            stmts.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        GrowthService(db).recalc_levels(admin)
    finally:
        event.remove(engine, "before_cursor_execute", before)

    assert len(stmts) == 1, f"应一次取全量已授节点（实 {len(stmts)} 条：{stmts}）"
    assert db.query(MilestoneAward).filter(MilestoneAward.is_deleted == 0).count() > 0


def test_quiz_overview_loads_only_referenced_books(db):
    """RED（全表加载 books）：成长档案每次都把全馆书目读进内存。"""
    child = _child(db, phone="13982026420")
    for i in range(20):
        _book(db, f"无关书{i}")
    book = _book(db, "被测验书")
    db.add(
        QuizAttempt(
            child_id=child.id,
            book_id=book.id,
            score=5,
            total_questions=5,
            submitted_at=datetime.now(),
        )
    )
    db.commit()

    stmts: list[str] = []

    def before(_conn, _cursor, statement, _params, _context, _executemany):
        if "FROM books" in statement:
            stmts.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        GrowthService(db).quiz_overview(child.id)
    finally:
        event.remove(engine, "before_cursor_execute", before)

    assert stmts, "没抓到 books 查询——判据塌缩"
    assert any("books.id IN" in s for s in stmts), (
        f"书名应只查引用到的书（RED=全表加载）；实捕获：{stmts}"
    )
