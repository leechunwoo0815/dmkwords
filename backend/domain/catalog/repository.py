# backend/domain/catalog/repository.py
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.catalog.models import Book, BookCopy, QuizQuestion


class BookRepository(BaseRepository[Book]):
    def __init__(self, db: Session):
        super().__init__(db, Book)

    def get_by_isbn(self, isbn: str) -> Book | None:
        return self.get_by_field("isbn", isbn)


class BookCopyRepository(BaseRepository[BookCopy]):
    def __init__(self, db: Session):
        super().__init__(db, BookCopy)

    def list_by_book(self, book_id: int) -> list[BookCopy]:
        return (
            self.db.query(BookCopy)
            .filter(BookCopy.book_id == book_id, BookCopy.is_deleted == 0)
            .order_by(BookCopy.id)
            .all()
        )

    def copy_counts_by_book(self, book_ids: list[int]) -> dict[int, int]:
        rows = (
            self.db.query(BookCopy.book_id, func.count(BookCopy.id))
            .filter(BookCopy.book_id.in_(book_ids), BookCopy.is_deleted == 0)
            .group_by(BookCopy.book_id)
            .all()
        )
        return {book_id: count for book_id, count in rows}

    def next_copy_code(self, book: Book, seq: int | None = None) -> str:
        """副本码：C{ISBN后8位}-{序号:03d}。

        序号取当前最大序号 +1（含软删行，避免唯一索引撞码）；
        批量创建时用 seq 显式递增（同事务 count 不增长）。
        """
        base = (book.isbn or book.internal_code or "BK")[-8:]
        if seq is not None:
            return f"C{base}-{seq:03d}"
        all_codes = self.db.query(BookCopy.copy_code).filter(BookCopy.book_id == book.id).all()
        max_seq = 0
        prefix = f"C{base}-"
        for (code,) in all_codes:
            if code and code.startswith(prefix):
                try:
                    max_seq = max(max_seq, int(code[len(prefix) :]))
                except ValueError:
                    continue
        return f"C{base}-{max_seq + 1:03d}"


class QuizQuestionRepository(BaseRepository[QuizQuestion]):
    def __init__(self, db: Session):
        super().__init__(db, QuizQuestion)

    def list_by_book(self, book_id: int, active_only: bool = True) -> list[QuizQuestion]:
        q = self.db.query(QuizQuestion).filter(
            QuizQuestion.book_id == book_id, QuizQuestion.is_deleted == 0
        )
        if active_only:
            q = q.filter(QuizQuestion.is_active == 1)
        return q.order_by(QuizQuestion.sort_order, QuizQuestion.id).all()

    def question_counts_by_book(self, book_ids: list[int]) -> dict[int, int]:
        if not book_ids:
            return {}
        rows = (
            self.db.query(QuizQuestion.book_id, func.count(QuizQuestion.id))
            .filter(QuizQuestion.book_id.in_(book_ids), QuizQuestion.is_deleted == 0)
            .group_by(QuizQuestion.book_id)
            .all()
        )
        return {book_id: count for book_id, count in rows}

    def question_active_counts_by_book(self, book_ids: list[int]) -> dict[int, int]:
        """P2-5：启用题数（is_active=1）——与「测验未满 5 道」筛选同一口径。"""
        if not book_ids:
            return {}
        rows = (
            self.db.query(QuizQuestion.book_id, func.count(QuizQuestion.id))
            .filter(
                QuizQuestion.book_id.in_(book_ids),
                QuizQuestion.is_deleted == 0,
                QuizQuestion.is_active == 1,
            )
            .group_by(QuizQuestion.book_id)
            .all()
        )
        return {book_id: count for book_id, count in rows}
