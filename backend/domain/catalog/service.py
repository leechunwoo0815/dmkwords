# backend/domain/catalog/service.py — 图书资产管理
"""catalog 域服务。事务纪律：Service 统一 commit；操作留痕进审计日志。"""

from __future__ import annotations

import json

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.constants import ISBN_RE, clean_isbn
from backend.domain.catalog.models import Book, BookCopy, QuizQuestion
from backend.domain.catalog.repository import (
    BookCopyRepository,
    BookRepository,
    QuizQuestionRepository,
)
from backend.domain.catalog.schemas import BookCreateRequest, BookUpdateRequest

# P2-7：排序白名单（copy_count 需 join 子查询）
SORT_WHITELIST = {"id", "word_count", "copy_count"}


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

    def _filtered_query(
        self,
        keyword: str | None,
        ar_pending: bool,
        status: int | None,
        no_cover: bool = False,
        no_audio: bool = False,
        quiz_incomplete: bool = False,
    ):
        """C3：列表筛选的单一事实源——list_books 与 tab_counts 共用，防口径漂移。"""
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
            sub = self._active_quiz_subq()
            q = q.outerjoin(sub, sub.c.book_id == Book.id).filter(
                (sub.c.cnt.is_(None)) | (sub.c.cnt < 5)
            )
        return q

    def _active_quiz_subq(self):
        active_count = (
            self.db.query(QuizQuestion.book_id, func.count(QuizQuestion.id).label("cnt"))
            .filter(QuizQuestion.is_deleted == 0, QuizQuestion.is_active == 1)
            .group_by(QuizQuestion.book_id)
            .subquery()
        )
        return active_count

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
        sort: str | None = None,
        order: str | None = None,
    ):
        q = self._filtered_query(keyword, ar_pending, status, no_cover, no_audio, quiz_incomplete)

        # P2-7：受控排序；非法 sort/order 静默回默认 id desc
        if sort in SORT_WHITELIST:
            if sort == "copy_count":
                cnt = (
                    self.db.query(BookCopy.book_id, func.count(BookCopy.id).label("cnt"))
                    .filter(BookCopy.is_deleted == 0)
                    .group_by(BookCopy.book_id)
                    .subquery()
                )
                q = q.outerjoin(cnt, cnt.c.book_id == Book.id)
                col = func.coalesce(cnt.c.cnt, 0)
            else:
                col = Book.word_count if sort == "word_count" else Book.id
            q = q.order_by(col.asc() if order == "asc" else col.desc(), Book.id.desc())
        else:
            q = q.order_by(Book.id.desc())

        total = q.count()
        books = q.offset((page - 1) * page_size).limit(page_size).all()
        counts = self.copy_repo.copy_counts_by_book([b.id for b in books])
        return books, counts, total

    def tab_counts(self, keyword: str | None = None) -> dict[str, int]:
        """C3：7 个 Tab 计数——逐 Tab 复用 _filtered_query 同一筛选构造。

        all = 仅 is_deleted=0（不带 Tab 筛选）；keyword 为搜索条件，各口径一致生效，
        保证计数与用户当前看到的筛选结果一致。
        """

        def n(ar_pending=False, status=None, no_cover=False, no_audio=False, quiz_incomplete=False):
            return self._filtered_query(
                keyword, ar_pending, status, no_cover, no_audio, quiz_incomplete
            ).count()

        return {
            "all": self._filtered_query(keyword, False, None).count(),
            "on": n(status=1),
            "off": n(status=0),
            "ar": n(ar_pending=True),
            "no_cover": n(no_cover=True),
            "no_audio": n(no_audio=True),
            "quiz_incomplete": n(quiz_incomplete=True),
        }

    def get_book(self, book_id: int) -> tuple[Book, list[BookCopy]]:
        book = self.book_repo.get_by_id_or_raise(book_id)
        copies = self.copy_repo.list_by_book(book_id)
        return book, copies

    def create_book(self, admin, req: BookCreateRequest) -> Book:
        isbn = clean_isbn(req.isbn)
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
            # D1：新书一律下架入库——完善封面/音频/AR/测验后再上架
            status=Book.STATUS_OFF,
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
        # ISBN 可后补/修改：清洗 + 校验格式 + 唯一性（不含自己）
        new_isbn = clean_isbn(req.isbn) or None
        if new_isbn != book.isbn:
            if new_isbn and not ISBN_RE.match(new_isbn):
                raise ValidationError(f"ISBN 格式不正确: {new_isbn}")
            if new_isbn and self.book_repo.get_by_isbn(new_isbn):
                raise ConflictError(f"ISBN {new_isbn} 已存在（补货请走副本管理增加副本）")
            book.isbn = new_isbn
        # R1：清空 ISBN 后若从未有内部编号（创建时带 ISBN），补生成，保证 book_code 恒非空
        if not book.isbn and not book.internal_code:
            book.internal_code = f"LOCAL-{book.id:06d}"
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

    def batch_toggle_status(self, admin, book_ids: list[int], status: int) -> dict:
        """Batch set book status to on (1) or off (0). D1：目标上架逐本校验，失败进明细（部分成功）。"""
        success = 0
        errors: list[str] = []
        for book_id in book_ids:
            try:
                book = self.book_repo.get_by_id_or_raise(book_id)
                if status == 1:
                    self._assert_can_onboard(book)
                book.status = status
                self.book_repo.update(book)
                self._audit(
                    admin,
                    "book.batch_toggle_status",
                    str(book.id),
                    {"new_status": status},
                )
                self.db.commit()
                success += 1
            except Exception as exc:
                errors.append(f"ID {book_id}: {exc}")
        return {"success": success, "failed": len(errors), "errors": errors[:50]}

    def _onboarding_missing(self, book: Book) -> list[str]:
        """D1：上架完整性五项检查——封面/音频/AR/词数≥1/启用测验题≥5，返回中文缺失清单。"""
        missing: list[str] = []
        if not book.cover_path:
            missing.append("未传封面")
        if not book.audio_path:
            missing.append("未传音频")
        if not book.ar_level:
            missing.append("未配置 AR 值")
        if not book.word_count or book.word_count < 1:
            missing.append("词数无效（需≥1）")
        active = (
            self.db.query(func.count(QuizQuestion.id))
            .filter(
                QuizQuestion.book_id == book.id,
                QuizQuestion.is_deleted == 0,
                QuizQuestion.is_active == 1,
            )
            .scalar()
        )
        if active < 5:
            missing.append(f"未满 5 道测验题（当前 {active} 道）")
        return missing

    def _assert_can_onboard(self, book: Book) -> None:
        """D1：上架拦截。配置 book_onboarding_check=false 时跳过（演示/特殊场景）。"""
        from backend.common.config_service import ConfigService

        if ConfigService(self.db).get_value("book_onboarding_check", "true").lower() != "true":
            return
        missing = self._onboarding_missing(book)
        if missing:
            raise ConflictError(f"无法上架：《{book.title}》{'、'.join(missing)}")

    def get_onboarding_missing(self, book_id: int) -> list[str]:
        """D1：详情接口缺失清单——仅下架态计算，上架态恒空。"""
        book = self.book_repo.get_by_id_or_raise(book_id)
        if book.status != Book.STATUS_OFF:
            return []
        return self._onboarding_missing(book)

    def add_copies(self, admin, book_id: int, count: int) -> list[BookCopy]:
        from sqlalchemy.exc import IntegrityError

        book = self.book_repo.get_by_id_or_raise(book_id)
        next_seq = self._max_copy_seq(book) + 1
        created = [
            BookCopy(book_id=book.id, copy_code=self.copy_repo.next_copy_code(book, next_seq + i))
            for i in range(count)
        ]
        try:
            self.copy_repo.bulk_create(created)
        except IntegrityError:
            # P2-10：并发下撞 uq_copy_code → 回滚并转业务异常（禁止裸 500）
            self.db.rollback()
            raise ConflictError("副本编码冲突（可能他人正在操作此书），请重试") from None
        self._audit(admin, "book.add_copies", str(book.id), {"added": count})
        self.db.commit()
        return created

    def toggle_status(self, admin, book_id: int) -> Book:
        """上下架切换。下架影响：隐藏/禁借/停音频/禁新测验；已借仍可还；词数不回收。
        D1：下架→上架方向做完整性校验（开关 book_onboarding_check 可关）；上架→下架不校验。"""
        book = self.book_repo.get_by_id_or_raise(book_id)
        if book.status == Book.STATUS_OFF:
            self._assert_can_onboard(book)
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
        """封面上传：统一转 JPG 存储（Pillow），路径 cover/{isbn前4}/{code}_{token}.jpg。
        R2：旧文件只在 commit 成功后删除（对齐 delete_book），防 DB 指向已删文件。"""
        book = self.book_repo.get_by_id_or_raise(book_id)
        from backend.common.file_storage import remove_book_media, save_cover_jpg

        old = book.cover_path
        book.cover_path = save_cover_jpg(book, data, ext)
        self.book_repo.update(book)
        self._audit(admin, "book.cover", str(book.id), {"path": book.cover_path})
        self.db.commit()
        if old and old != book.cover_path:
            remove_book_media(old, None)
        return book

    def upload_audio(self, admin, book_id: int, data: bytes, filename: str) -> Book:
        """音频上传：仅 MP3；路径 book_audio/{code}/audio_{token}.mp3；解析时长。
        R2：旧文件只在 commit 成功后删除（对齐 delete_book），防 DB 指向已删文件。"""
        if not filename.lower().endswith(".mp3"):
            raise ValidationError("音频仅支持 MP3 格式")
        book = self.book_repo.get_by_id_or_raise(book_id)
        from backend.common.file_storage import remove_book_media, save_audio_mp3

        old = book.audio_path
        book.audio_path, duration = save_audio_mp3(book, data)
        book.audio_duration_seconds = duration
        self.book_repo.update(book)
        self._audit(
            admin, "book.audio", str(book.id), {"path": book.audio_path, "duration": duration}
        )
        self.db.commit()
        if old and old != book.audio_path:
            remove_book_media(None, old)
        return book


class QuizQuestionService:
    def __init__(self, db: Session):
        self.db = db
        self.question_repo = QuizQuestionRepository(db)

    def _audit(self, admin, action: str, book_id: int, question_id: int, detail: dict) -> None:
        """R9：题目写操作留痕（影响 WM7 计分，必须可追溯）。"""
        publish_audit(
            self.db,
            admin=admin,
            action=action,
            target_type="book",
            target_id=str(book_id),
            detail={"question_id": question_id, **detail},
            reason="测验题库",
        )

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
        self._audit(
            admin,
            "quiz.create",
            book_id,
            q.id,
            {"question_type": q.question_type, "question_text": q.question_text},
        )
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
        self._audit(
            admin,
            "quiz.update",
            q.book_id,
            q.id,
            {
                "question_type": q.question_type,
                "question_text": q.question_text,
                "answer": q.answer,
            },
        )
        self.db.commit()
        return q

    def toggle_active(self, admin, question_id: int) -> QuizQuestion:
        q = self.question_repo.get_by_id_or_raise(question_id)
        q.is_active = 0 if q.is_active == 1 else 1
        self.question_repo.update(q)
        self._audit(admin, "quiz.toggle", q.book_id, q.id, {"is_active": q.is_active})
        self.db.commit()
        return q

    def delete(self, admin, question_id: int) -> None:
        q = self.question_repo.get_by_id_or_raise(question_id)
        self.question_repo.soft_delete(question_id)
        self._audit(admin, "quiz.delete", q.book_id, q.id, {"question_text": q.question_text})
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
    guide.append(["副本数", "否", "空=1；范围 1-99"])
    guide.append([])
    guide.append(["示例行（正式导入时请删除或改为真实数据）"])
    guide.append(
        ["9780545582889", "示例书名", "示例作者", "3.5", "1200", "动物", "7-8岁（小学低年级）", "1"]
    )

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
