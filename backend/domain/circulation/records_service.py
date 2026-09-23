# backend/domain/circulation/records_service.py — 借还记录查询与导出（2026-09-21 任务包 D 批）
"""为什么单独一个文件：`service.py` 已 793 行、贴到架构关的单文件 800 行上限（god-file 规则，
本轮之前活动域就为此拆过 `detail_blocks.py` / `past_service.py`）。借还记录是**只读查询**，
与借/还/续的写路径互不牵连，切开最省事也最安全。

口径（用户拍板）：**要"归还操作人"**（`borrow_records.returned_by`，本轮加列）+ **Excel 导出**；
列表要能按 关键词（孩子名 / 家长手机号 / 书名 / 副本码）、状态、时间段（借出或归还）过滤。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.common.exceptions import ValidationError
from backend.common.sql_utils import escape_like
from backend.common.system_models import AuditLog
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent

_STATUSES = (
    BorrowRecord.STATUS_ACTIVE,
    BorrowRecord.STATUS_OVERDUE,
    BorrowRecord.STATUS_RETURNED,
    BorrowRecord.STATUS_LOST,
)
_UNRECORDED = "未记录"  # 历史行（加列前）确实没记操作人——如实展示，不编造


class BorrowRecordsService:
    """借还记录查询（分页 + 多条件）与 Excel 导出。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_records(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        status: str | None = None,
        date_field: str = "borrowed",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[dict], int]:
        if date_field not in ("borrowed", "returned"):
            raise ValidationError("date_field 只支持 borrowed / returned")
        if status and status not in _STATUSES:
            raise ValidationError("status 不合法")
        q = (
            self.db.query(BorrowRecord, Child, Parent, Book, BookCopy)
            .join(Child, Child.id == BorrowRecord.child_id)
            .join(Parent, Parent.id == Child.parent_id)
            .join(Book, Book.id == BorrowRecord.book_id)
            .outerjoin(BookCopy, BookCopy.id == BorrowRecord.copy_id)
            .filter(BorrowRecord.is_deleted == 0)
        )
        kw = (keyword or "").strip()
        if kw:
            like = f"%{escape_like(kw)}%"
            q = q.filter(
                or_(
                    Child.name.like(like, escape="\\"),
                    Child.english_name.like(like, escape="\\"),
                    Parent.phone.like(like, escape="\\"),
                    Book.title.like(like, escape="\\"),
                    Book.isbn.like(like, escape="\\"),
                    BookCopy.copy_code.like(like, escape="\\"),
                )
            )
        if status:
            q = q.filter(BorrowRecord.status == status)
        col = BorrowRecord.borrowed_at if date_field == "borrowed" else BorrowRecord.returned_at
        if date_from:
            q = q.filter(col >= date_from)
        if date_to:
            q = q.filter(col <= date_to)
        total = q.count()
        rows = (
            q.order_by(BorrowRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        )
        return self._shape(rows), total

    def _shape(self, rows) -> list[dict]:
        # 操作人姓名：**读审计表**，不从 admin_users 取——
        # ① 架构关铁律「业务域不得反向依赖 admin」（首版 import AdminUser 直接被门禁拦下）；
        # ② 借出/归还各有独立审计行（target_type="borrow" + target_id=记录 id）且自带 actor_name，
        #    连加列之前的历史行也能查到真实操作人；只有连审计都没有的种子数据才显示"未记录"。
        rec_ids = [rec.id for rec, *_ in rows]
        names: dict[tuple[int, str], str] = {}
        if rec_ids:
            audits = (
                self.db.query(AuditLog)
                .filter(
                    AuditLog.target_type == "borrow",
                    AuditLog.target_id.in_([str(i) for i in rec_ids]),
                    AuditLog.action.in_(["circulation.borrow", "circulation.return"]),
                )
                .order_by(AuditLog.id)
                .all()
            )
            for a in audits:  # 同动作多条时后写的覆盖先写的（order_by 保证取最新）
                names[(int(a.target_id), a.action)] = a.actor_name or _UNRECORDED
        now = datetime.now()
        out: list[dict] = []
        for rec, child, parent, book, copy in rows:
            out.append(
                {
                    "record_id": rec.id,
                    "child_id": child.id,
                    "child_name": child.name,
                    "parent_phone": parent.phone,
                    "book_id": book.id,
                    "book_title": book.title,
                    "copy_id": rec.copy_id,
                    "copy_code": copy.copy_code if copy else "",
                    "status": rec.status,
                    "borrowed_at": rec.borrowed_at,
                    "due_at": rec.due_at,
                    "returned_at": rec.returned_at,
                    "returned_condition": rec.returned_condition,
                    "renew_used": rec.renew_used,
                    "days_overdue": (
                        max(0, (now - rec.due_at).days)
                        if rec.status in (BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE)
                        else 0
                    ),
                    "borrowed_by_name": names.get((rec.id, "circulation.borrow")) or _UNRECORDED,
                    "returned_by_name": names.get((rec.id, "circulation.return")) or _UNRECORDED,
                }
            )
        return out

    def export_excel(self, **filters) -> bytes:
        """导出 Excel（openpyxl，与审计/通知导出同款出口）。

        **导出的是当前筛选结果**（不是全量）——用户在界面上筛完再点导出，导出的必须是他看到的那批；
        上限 10000 行，超出截断并在表头行后写明（诚实标注，不静默）。
        """
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Font

        rows, total = self.list_records(page=1, page_size=10000, **filters)
        wb = Workbook()
        ws = wb.active
        ws.title = "借还记录"
        headers = [
            "记录ID",
            "孩子",
            "家长电话",
            "书名",
            "副本码",
            "状态",
            "借出时间",
            "应还时间",
            "归还时间",
            "归还状态",
            "续借次数",
            "逾期天数",
            "借出操作人",
            "归还操作人",
        ]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append(
                [
                    r["record_id"],
                    r["child_name"],
                    r["parent_phone"],
                    r["book_title"],
                    r["copy_code"],
                    r["status"],
                    r["borrowed_at"].strftime("%Y-%m-%d %H:%M") if r["borrowed_at"] else "",
                    r["due_at"].strftime("%Y-%m-%d %H:%M") if r["due_at"] else "",
                    r["returned_at"].strftime("%Y-%m-%d %H:%M") if r["returned_at"] else "",
                    r["returned_condition"] or "",
                    r["renew_used"],
                    r["days_overdue"],
                    r["borrowed_by_name"],
                    r["returned_by_name"],
                ]
            )
        if total > len(rows):
            ws.append([])
            ws.append([f"注意：共 {total} 条，本次导出前 {len(rows)} 条（超出上限被截断）"])
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()
