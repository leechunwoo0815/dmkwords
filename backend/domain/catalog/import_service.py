# backend/domain/catalog/import_service.py — Excel 批量导入（R-316 / V1.1 §4.3）
"""逐行校验：错误行报"第 N 行：原因"，不影响其他行；同 ISBN = 增副本不重建书目。"""

from __future__ import annotations

import io

from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.constants import GRADE_OPTIONS, ISBN_RE, clean_isbn
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.catalog.repository import BookCopyRepository, BookRepository
from backend.domain.catalog.schemas import AR_LEVEL_MAX, AR_LEVEL_RE

# 与 build_import_template 表头保持一致；程序按列索引解析，不依赖字面量。
HEADER = ["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"]


def _cell_str(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _max_copy_seq(copy_repo: BookCopyRepository, book: Book) -> int:
    prefix = f"C{(book.isbn or book.internal_code or 'BK')[-8:]}-"
    codes = [
        c
        for (c,) in copy_repo.db.query(BookCopy.copy_code).filter(BookCopy.book_id == book.id).all()
    ]
    best = 0
    for code in codes:
        if code and code.startswith(prefix):
            try:
                best = max(best, int(code[len(prefix) :]))
            except ValueError:
                continue
    return best


def import_books(db: Session, admin, file_bytes: bytes) -> dict:
    """返回 {total, success, failed, errors: ["第N行: 原因"]}。"""
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    book_repo = BookRepository(db)
    copy_repo = BookCopyRepository(db)

    errors: list[str] = []
    success = 0
    row_num = 0
    created_books: list[dict] = []
    added_copies: list[dict] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        row_num += 1
        if not any(_cell_str(c) for c in row):
            continue  # 跳过空行
        isbn, title, author, ar, word_count, topic, grade, copies = (
            _cell_str(row[0]) if len(row) > 0 else "",
            _cell_str(row[1]) if len(row) > 1 else "",
            _cell_str(row[2]) if len(row) > 2 else "",
            _cell_str(row[3]) if len(row) > 3 else "",
            _cell_str(row[4]) if len(row) > 4 else "",
            _cell_str(row[5]) if len(row) > 5 else "",
            _cell_str(row[6]) if len(row) > 6 else "",
            _cell_str(row[7]) if len(row) > 7 else "1",
        )

        # ---- 行级校验 ----
        isbn = clean_isbn(isbn)  # P2-8：行级清洗（半角连字符/空白）
        if not title:
            errors.append(f"第{row_num}行: 书名不能为空")
            continue
        # G17（2026-10-15 第六十一批）：PRD §4.3「没有 ISBN 的书（极少数）：支持录入，系统自动生成
        # 内部编号，但**只能后台单本录入，不走 Excel**」。此前这里对空 ISBN 照建书目（internal_code=LOCAL-…），
        # 与 PRD 相反——且无 ISBN 行**没有去重依据**，批量导入必然产出一堆认不出来的重书。
        if not isbn:
            errors.append(f"第{row_num}行: 无 ISBN 的书只能后台单本录入，不走 Excel")
            continue
        if not ISBN_RE.match(isbn):
            errors.append(f"第{row_num}行: ISBN 格式不正确（{isbn}）")
            continue
        # R7：AR 值与 schema 同规则（格式 + 0-12.9），防脏值漏进「AR 待配置」筛选
        if ar:
            if not AR_LEVEL_RE.match(ar):
                errors.append(f"第{row_num}行: AR 值格式不正确（{ar}）")
                continue
            if float(ar) > AR_LEVEL_MAX:
                errors.append(f"第{row_num}行: AR 值超出范围（{ar}），上限 {AR_LEVEL_MAX}")
                continue
        try:
            wc = int(float(word_count)) if word_count else 0
            # P2-12：0 词书目无业务意义（WM7 入账依据），最小 1
            if wc < 1:
                raise ValueError
        except ValueError:
            errors.append(f"第{row_num}行: 总词数必须是正整数")
            continue
        try:
            copy_n = int(float(copies)) if copies else 1
            if copy_n < 1 or copy_n > 99:
                raise ValueError
        except ValueError:
            errors.append(f"第{row_num}行: 副本数量须在 1-99 之间")
            continue
        if grade and grade not in GRADE_OPTIONS:
            errors.append(f"第{row_num}行: 适读阶段不在选项中（{grade}）")
            continue

        if isbn:
            existing = book_repo.get_by_isbn(isbn)
            if existing:
                # 同 ISBN = 增副本（V1.1 §4.1）
                seq = _max_copy_seq(copy_repo, existing) + 1
                # P2-20（2026-10-17 第六十九批·大审查）：副本码全局唯一（ISBN 后 8 位），
                # 前缀录错撞码时原先直抛 IntegrityError → 整批 500、**整批结果全丢**。
                # SAVEPOINT 把冲突锁在单行：记一条行级错误，继续处理其余行。
                try:
                    with db.begin_nested():
                        for i in range(copy_n):
                            copy_repo.create(
                                BookCopy(
                                    book_id=existing.id,
                                    copy_code=copy_repo.next_copy_code(existing, seq + i),
                                )
                            )
                except IntegrityError:
                    errors.append(
                        f"第{row_num}行: 副本码冲突（ISBN 后 8 位与已有书目相同）——请核对 ISBN"
                    )
                    continue
                added_copies.append({"isbn": isbn, "added": copy_n})
                success += 1
                continue
            # P1-4 同族（2026-10-18 第七十五批）：`ix_books_isbn` 不含 is_deleted，
            # 软删书目仍占用该 ISBN；上面的 get_by_isbn 过滤软删 → 落到这里建新行会撞 1062。
            # 批量导入是"行级报错"语义：如实点名冲突，让馆员自己决定恢复还是改号（不静默复活）。
            ghost = book_repo.get_by_isbn_including_deleted(isbn)
            if ghost is not None:
                errors.append(
                    f"第{row_num}行: ISBN {isbn} 属于一本已删除的书目（《{ghost.title}》，"
                    f"id={ghost.id}）——请在图书管理里用该 ISBN 新建以恢复，或改用其他 ISBN"
                )
                continue

        book = Book(
            isbn=isbn or None,
            title=title,
            author=author,
            word_count=wc,
            ar_level=ar or None,
            topic=topic,
            grade=grade,
            # D1：导入一律下架入库——馆员逐本完善后再上架
            status=Book.STATUS_OFF,
        )
        # P2-20：新书这一行同样用 SAVEPOINT 隔离（副本码冲突只废掉该行，书目行一并回滚，
        # 不留"没有副本的半本书"，其余行照常导入）。
        try:
            with db.begin_nested():
                book_repo.create(book)
                if not isbn:
                    book.internal_code = f"LOCAL-{book.id:06d}"
                    book_repo.update(book)
                seq = _max_copy_seq(copy_repo, book) + 1
                for i in range(copy_n):
                    copy_repo.create(
                        BookCopy(book_id=book.id, copy_code=copy_repo.next_copy_code(book, seq + i))
                    )
        except IntegrityError:
            errors.append(f"第{row_num}行: 副本码冲突（ISBN 后 8 位与已有书目相同）——请核对 ISBN")
            continue
        created_books.append({"title": title, "isbn": isbn})
        success += 1

    if created_books or added_copies:
        publish_audit(
            db,
            admin=admin,
            action="book.import",
            target_type="book",
            target_id="batch",
            detail={
                "created": len(created_books),
                "copies_added": len(added_copies),
                "failed": len(errors),
            },
            reason="Excel 批量导入",
        )
    db.commit()
    wb.close()
    return {
        "total_rows": row_num,
        "success_count": success,
        "failed_count": len(errors),
        "errors": errors[:50],
    }
