# backend/domain/catalog/service.py — 图书资产管理
"""catalog 域服务。事务纪律：Service 统一 commit；操作留痕进审计日志。"""

from __future__ import annotations

import json
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.admin.models import AuditLog
from backend.domain.admin.repository import AuditLogRepository
from backend.domain.catalog.models import Book, BookCopy, QuizQuestion
from backend.domain.catalog.repository import (
    BookCopyRepository,
    BookRepository,
    QuizQuestionRepository,
)
from backend.domain.catalog.schemas import BookCreateRequest, BookUpdateRequest

ISBN_RE = re.compile(r"^\d{9}[\dXx]$|^\d{13}$")


class BookService:
    def __init__(self, db: Session):
        self.db = db
        self.book_repo = BookRepository(db)
        self.copy_repo = BookCopyRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def _max_copy_seq(self, book: Book) -> int:
        prefix = f"C{(book.isbn or book.internal_code or 'BK')[-8:]}-"
        codes = [
            c
            for (c,) in self.db.query(BookCopy.copy_code).filter(BookCopy.book_id == book.id).all()
        ]
        best = 0
        for code in codes:
            if code and code.startswith(prefix):
                try:
                    best = max(best, int(code[len(prefix) :]))
                except ValueError:
                    continue
        return best

    def _audit(self, admin, action: str, target: str, detail: dict, reason: str = "") -> None:
        self.audit_repo.create(
            AuditLog(
                actor_id=admin.id,
                actor_name=admin.display_name or admin.username,
                action=action,
                target_type="book",
                target_id=target,
                detail=json.dumps(detail, ensure_ascii=False),
                reason=reason or "图书管理",
            )
        )

    def list_books(
        self, page: int, page_size: int, keyword: str | None, ar_pending: bool, status: int | None
    ):
        q = self.db.query(Book).filter(Book.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Book.title.like(like), Book.author.like(like), Book.isbn.like(like)))
        if ar_pending:
            q = q.filter(Book.ar_level.is_(None))
        if status is not None:
            q = q.filter(Book.status == status)
        total = q.count()
        books = q.order_by(Book.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        counts = self.copy_repo.copy_counts_by_book([b.id for b in books])
        return books, counts, total

    def get_book(self, book_id: int) -> tuple[Book, list[BookCopy]]:
        book = self.book_repo.get_by_id_or_raise(book_id)
        copies = self.copy_repo.list_by_book(book_id)
        return book, copies

    def create_book(self, admin, req: BookCreateRequest) -> Book:
        isbn = req.isbn.strip() if req.isbn else ""
        if isbn:
            if not ISBN_RE.match(isbn):
                raise ValidationError(f"ISBN 格式不正确: {isbn}")
            if self.book_repo.get_by_isbn(isbn):
                raise ConflictError(f"ISBN {isbn} 已存在（补货请走副本管理增加副本）")
        book = Book(
            isbn=isbn or None,
            internal_code=None,
            title=req.title.strip(),
            author=req.author.strip(),
            word_count=req.word_count,
            ar_level=req.ar_level,
            topic=req.topic,
            grade=req.grade,
            description=req.description,
            status=Book.STATUS_ON,
        )
        self.book_repo.create(book)
        # 无 ISBN 书目生成内部编号
        if not isbn:
            book.internal_code = f"LOCAL-{book.id:06d}"
            self.book_repo.update(book)
        next_seq = self._max_copy_seq(book) + 1
        for i in range(req.copy_count):
            self.copy_repo.create(
                BookCopy(
                    book_id=book.id, copy_code=self.copy_repo.next_copy_code(book, next_seq + i)
                )
            )
        self._audit(admin, "book.create", str(book.id), {"title": book.title, "isbn": isbn})
        self.db.commit()
        return book

    def update_book(self, admin, book_id: int, req: BookUpdateRequest) -> Book:
        book = self.book_repo.get_by_id_or_raise(book_id)
        old = {"title": book.title, "word_count": book.word_count, "ar_level": book.ar_level}
        book.title = req.title.strip()
        book.author = req.author.strip()
        book.word_count = req.word_count
        book.ar_level = req.ar_level
        book.topic = req.topic
        book.grade = req.grade
        book.description = req.description
        self.book_repo.update(book)
        self._audit(
            admin,
            "book.update",
            str(book.id),
            {
                "old": old,
                "new": {
                    "title": book.title,
                    "word_count": book.word_count,
                    "ar_level": book.ar_level,
                },
            },
        )
        self.db.commit()
        return book

    def add_copies(self, admin, book_id: int, count: int) -> list[BookCopy]:
        book = self.book_repo.get_by_id_or_raise(book_id)
        next_seq = self._max_copy_seq(book) + 1
        created = [
            BookCopy(book_id=book.id, copy_code=self.copy_repo.next_copy_code(book, next_seq + i))
            for i in range(count)
        ]
        self.copy_repo.bulk_create(created)
        self._audit(admin, "book.add_copies", str(book.id), {"added": count})
        self.db.commit()
        return created

    def toggle_status(self, admin, book_id: int) -> Book:
        """上下架切换。下架影响：隐藏/禁借/停音频/禁新测验；已借仍可还；词数不回收。"""
        book = self.book_repo.get_by_id_or_raise(book_id)
        book.status = Book.STATUS_OFF if book.status == Book.STATUS_ON else Book.STATUS_ON
        self.book_repo.update(book)
        self._audit(admin, "book.toggle_status", str(book.id), {"new_status": book.status})
        self.db.commit()
        return book

    def update_copy_status(self, admin, copy_id: int, new_status: str, reason: str) -> BookCopy:
        copy = self.copy_repo.get_by_id_or_raise(copy_id)
        if not copy.can_transition(new_status):
            raise ValidationError(
                f"副本状态不允许从 {copy.status} 变更为 {new_status}（转移矩阵拦截）"
            )
        old = copy.status
        copy.status = new_status
        self.copy_repo.update(copy)
        self._audit(
            admin,
            "copy.status",
            str(copy.id),
            {"old": old, "new": new_status, "copy_code": copy.copy_code},
            reason,
        )
        self.db.commit()
        return copy

    def upload_cover(self, admin, book_id: int, data: bytes, ext: str) -> Book:
        """封面上传：统一转 JPG 存储（Pillow），路径 cover/{isbn前4}/{code}.jpg。"""
        book = self.book_repo.get_by_id_or_raise(book_id)
        from backend.common.file_storage import save_cover_jpg

        book.cover_path = save_cover_jpg(book, data, ext)
        self.book_repo.update(book)
        self._audit(admin, "book.cover", str(book.id), {"path": book.cover_path})
        self.db.commit()
        return book

    def upload_audio(self, admin, book_id: int, data: bytes, filename: str) -> Book:
        """音频上传：仅 MP3；路径 book_audio/{code}/audio.mp3；解析时长。"""
        if not filename.lower().endswith(".mp3"):
            raise ValidationError("音频仅支持 MP3 格式")
        book = self.book_repo.get_by_id_or_raise(book_id)
        from backend.common.file_storage import save_audio_mp3

        duration = save_audio_mp3(book, data)
        book.audio_path = f"book_audio/{book.book_code}/audio.mp3"
        book.audio_duration_seconds = duration
        self.book_repo.update(book)
        self._audit(
            admin, "book.audio", str(book.id), {"path": book.audio_path, "duration": duration}
        )
        self.db.commit()
        return book


class QuizQuestionService:
    def __init__(self, db: Session):
        self.db = db
        self.question_repo = QuizQuestionRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def list_by_book(self, book_id: int) -> list[QuizQuestion]:
        return self.question_repo.list_by_book(book_id, active_only=False)

    def create(self, admin, book_id: int, req) -> QuizQuestion:
        if req.question_type == QuizQuestion.TYPE_BOOLEAN:
            valid_options = ["对", "错"]
            if [o.strip() for o in req.options] != valid_options:
                raise ValidationError("判断题选项固定为 [对, 错]")
        else:
            if len(req.options) < 2:
                raise ValidationError("单选题至少 2 个选项")
        if req.answer not in req.options:
            raise ValidationError("正确答案必须是选项之一")
        q = QuizQuestion(
            book_id=book_id,
            question_type=req.question_type,
            question_text=req.question_text.strip(),
            options=json.dumps(req.options, ensure_ascii=False),
            answer=req.answer,
            sort_order=req.sort_order,
        )
        self.question_repo.create(q)
        self.db.commit()
        return q

    def toggle_active(self, admin, question_id: int) -> QuizQuestion:
        q = self.question_repo.get_by_id_or_raise(question_id)
        q.is_active = 0 if q.is_active == 1 else 1
        self.question_repo.update(q)
        self.db.commit()
        return q

    def delete(self, admin, question_id: int) -> None:
        q = self.question_repo.get_by_id_or_raise(question_id)
        self.question_repo.soft_delete(question_id)
        self.db.commit()
