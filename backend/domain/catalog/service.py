# backend/domain/catalog/service.py — 图书资产管理
"""catalog 域服务。事务纪律：Service 统一 commit；操作留痕进审计日志。"""

from __future__ import annotations

import json
import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
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
        publish_audit(
            self.db,
            admin=admin,
            action=action,
            target_type="book",
            target_id=target,
            detail=detail,
            reason=reason or "图书管理",
        )

    def list_books(
        self,
        page: int,
        page_size: int,
        keyword: str | None,
        ar_pending: bool,
        status: int | None,
        no_cover: bool = False,
        no_audio: bool = False,
        quiz_incomplete: bool = False,
    ):
        from sqlalchemy import func

        q = self.db.query(Book).filter(Book.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                or_(
                    Book.title.like(like),
                    Book.author.like(like),
                    Book.isbn.like(like),
                    Book.internal_code.like(like),
                )
            )
        if ar_pending:
            q = q.filter(Book.ar_level.is_(None))
        if status is not None:
            q = q.filter(Book.status == status)
        if no_cover:
            q = q.filter(Book.cover_path.is_(None))
        if no_audio:
            q = q.filter(Book.audio_path.is_(None))
        if quiz_incomplete:
            from backend.domain.catalog.models import QuizQuestion

            active_count = (
                self.db.query(
                    QuizQuestion.book_id, func.count(QuizQuestion.id).label("cnt")
                )
                .filter(QuizQuestion.is_deleted == 0, QuizQuestion.is_active == 1)
                .group_by(QuizQuestion.book_id)
                .subquery()
            )
            q = q.outerjoin(active_count, active_count.c.book_id == Book.id).filter(
                (active_count.c.cnt.is_(None)) | (active_count.c.cnt < 5)
            )
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
        old = {
            "title": book.title,
            "word_count": book.word_count,
            "ar_level": book.ar_level,
            "isbn": book.isbn,
        }
        # ISBN 可后补/修改：校验格式 + 唯一性（不含自己）
        new_isbn = (req.isbn or "").strip() or None
        if new_isbn != book.isbn:
            if new_isbn and not ISBN_RE.match(new_isbn):
                raise ValidationError(f"ISBN 格式不正确: {new_isbn}")
            if new_isbn and self.book_repo.get_by_isbn(new_isbn):
                raise ConflictError(f"ISBN {new_isbn} 已存在（补货请走副本管理增加副本）")
            book.isbn = new_isbn
        book.title = req.title.strip()
        book.author = req.author.strip()
        book.word_count = req.word_count
        # C24：空串规范为 NULL（保证「AR 待配置」筛选 ar_level IS NULL 命中）
        book.ar_level = (req.ar_level or "").strip() or None
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
                    "isbn": book.isbn,
                },
            },
        )
        self.db.commit()
        return book

    def delete_book(self, admin, book_id: int) -> None:
        """软删书目（仅无在借副本时允许）；联动删除 uploads 中的封面与音频。"""
        from backend.common.file_storage import remove_book_media

        book = self.book_repo.get_by_id_or_raise(book_id)
        borrowed = (
            self.db.query(BookCopy)
            .filter(BookCopy.book_id == book_id, BookCopy.status == BookCopy.STATUS_BORROWED)
            .count()
        )
        if borrowed:
            raise ConflictError(f"该书目存在 {borrowed} 本借出中副本，请先归还再删除")
        book.is_deleted = 1
        self.book_repo.update(book)
        self._audit(
            admin,
            "book.delete",
            str(book.id),
            {"title": book.title, "isbn": book.isbn, "internal_code": book.internal_code},
        )
        self.db.commit()
        remove_book_media(book.cover_path, book.audio_path)

    def batch_delete_books(self, admin, book_ids: list[int]) -> dict:
        """Batch soft-delete books; delegate per-book delete_book checks."""
        success = 0
        errors: list[str] = []
        for book_id in book_ids:
            try:
                self.delete_book(admin, book_id)
                success += 1
            except Exception as exc:
                errors.append(f"ID {book_id}: {exc}")
        return {"success": success, "failed": len(errors), "errors": errors[:50]}

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

    def list_by_book(self, book_id: int) -> list[QuizQuestion]:
        return self.question_repo.list_by_book(book_id, active_only=False)

    def _validate_question(self, question_type: str, options: list[str], answer: str) -> list[str]:
        if question_type == QuizQuestion.TYPE_BOOLEAN:
            valid_options = ["对", "错"]
            if [o.strip() for o in options] != valid_options:
                raise ValidationError("判断题选项固定为 [对, 错]")
        else:
            if len(options) < 2:
                raise ValidationError("单选题至少 2 个选项")
        if answer not in options:
            raise ValidationError("正确答案必须是选项之一")
        return answer

    def create(self, admin, book_id: int, req) -> QuizQuestion:
        self._validate_question(req.question_type, req.options, req.answer)
        # C29：序号由服务端分配（max(sort_order)+1），前端不再传（避免删除后重号）
        max_sort = (
            self.db.query(func.max(QuizQuestion.sort_order))
            .filter(
                QuizQuestion.book_id == book_id,
                QuizQuestion.is_deleted == 0,
            )
            .scalar()
        )
        q = QuizQuestion(
            book_id=book_id,
            question_type=req.question_type,
            question_text=req.question_text.strip(),
            options=json.dumps(req.options, ensure_ascii=False),
            answer=req.answer,
            sort_order=(max_sort or 0) + 1,
        )
        self.question_repo.create(q)
        self.db.commit()
        return q

    def update(self, admin, question_id: int, req) -> QuizQuestion:
        """C28：编辑题目（题干/类型/选项/答案；sort_order 不动）。"""
        q = self.question_repo.get_by_id_or_raise(question_id)
        self._validate_question(req.question_type, req.options, req.answer)
        q.question_type = req.question_type
        q.question_text = req.question_text.strip()
        q.options = json.dumps(req.options, ensure_ascii=False)
        q.answer = req.answer
        self.question_repo.update(q)
        self.db.commit()
        return q

    def toggle_active(self, admin, question_id: int) -> QuizQuestion:
        q = self.question_repo.get_by_id_or_raise(question_id)
        q.is_active = 0 if q.is_active == 1 else 1
        self.question_repo.update(q)
        self.db.commit()
        return q

    def delete(self, admin, question_id: int) -> None:
        self.question_repo.get_by_id_or_raise(question_id)
        self.question_repo.soft_delete(question_id)
        self.db.commit()


IMPORT_TEMPLATE_HEADERS = [
    "ISBN",
    "书名*",
    "作者",
    "AR值",
    "词数*",
    "主题",
    "适读阶段",
    "副本数",
]


def build_import_template() -> bytes:
    """生成 Excel 导入模板（C8）。
    活动 sheet 仅表头+空行（import_books 按行解析，示例/说明若在活动 sheet 会被当数据导入）；
    示例与说明放第二个 sheet「填写说明」。"""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation

    from backend.domain.catalog.constants import GRADE_OPTIONS

    wb = Workbook()
    ws = wb.active
    ws.title = "图书导入"
    ws.append(IMPORT_TEMPLATE_HEADERS)
    for _ in range(3):
        ws.append([])
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 10
    ws.column_dimensions["F"].width = 12
    ws.column_dimensions["G"].width = 20
    ws.column_dimensions["H"].width = 10

    # 适读阶段下拉（G 列，除表头外全部单元格）
    grade_dv = DataValidation(
        type="list",
        formula1=f'"{",".join(GRADE_OPTIONS)}"',
        allow_blank=True,
    )
    grade_dv.error = "请从下拉选项中选择适读阶段"
    grade_dv.errorTitle = "输入错误"
    grade_dv.prompt = "请选择适读阶段"
    grade_dv.promptTitle = "适读阶段"
    ws.add_data_validation(grade_dv)
    grade_dv.add("G2:G1048576")

    guide = wb.create_sheet("填写说明")
    guide.append(["列", "是否必填", "说明"])
    guide.append(["ISBN", "建议", "重复 ISBN 不重复建书，只加副本；图书唯一性最优解"])
    guide.append(["书名", "必填", "其余列可空，管理端补录"])
    guide.append(["作者", "否", ""])
    guide.append(["AR值", "否", "与孩子 AR 差值超范围时借书仅提示"])
    guide.append(["词数", "是", "正整数"])
    guide.append(["主题", "否", ""])
    guide.append(["适读阶段", "否", "请从下拉选项中选择；管理端也统一为阶段"])
    guide.append(["副本数", "否", "空=1；范围 0-999"])
    guide.append([])
    guide.append(["示例行（正式导入时请删除或改为真实数据）"])
    guide.append(
        ["9780545582889", "示例书名", "示例作者", "3.5", "1200", "动物", "7-8岁（小学低年级）", "1"]
    )

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
