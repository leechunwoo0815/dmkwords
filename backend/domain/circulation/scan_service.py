# backend/domain/circulation/service.py 的拆分（2026-09-21 C 批，与 records_service.py 同族手法）：
# `service.py` 加进扫码判定后 849 行、超架构关 800 行上限（god-file 规则）→ 把**扫码判定链**整体搬来。
# 这里只做"分流"，借书/还书/核销全部调用既有服务方法，不复制任何业务规则。
"""扫码统一判定（借阅台"扫会员码 → 连扫 ISBN"）。

**判定顺序就是产品口径，别改顺序**：
  ① 会员码 → 回这个孩子的卡片（馆员扫错框也能就地纠正）
  ② 形似会员码但校验位不过 → 422「码不合法」（别落进 ISBN 分支报"未入库"，那是误导）
  ③ 按 ISBN 找书目（用户拍板：馆内基本单副本，书码 = ISBN）
  ④ 本孩子名下这本书**有进行中记录** → 还书（"已借出去的，扫码就是还书"）
  ⑤ 该副本正被**当前孩子自己**的有效预约锁着 → 核销预约并借出（"自己预约的可以直接借"）
  ⑥ 有在馆副本 → 标准借书链（会员/押金/额度/同书未还/AR 提示全量校验）
  ⑦ 其余 → 按原因拦截：别人借出 / 别人预约锁定 / 维护 / 遗失 / 无在馆副本

为什么 ⑤ 不塞进 `borrow()`：reserved 副本"只有预约核销能动"是一条不变量，
扫码端点负责**分流**到既有 checkout，而不是放宽 borrow 的守卫（红线 R2）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.circulation.service import CirculationService
from backend.domain.identity.models import Child


class ScanService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.circ = CirculationService(db)

    def scan(self, admin, child_id: int, code: str) -> dict:
        """扫一个码 → 自动决定"这是谁 / 该借还是该还"（前端不猜，判定只在服务端）。

        **判定顺序就是口径，别改顺序**：
          ① 会员码 → 直接回这个孩子的卡片（馆员扫错框也能就地纠正，不必回第一个框重扫）
          ② 按 ISBN 找书目（用户拍板：馆内基本单副本，书码 = ISBN；多副本将来再改造）
          ③ 本孩子名下这本书**有进行中记录** → 还书（"如果是已借出去的，扫码就是还书"）
          ④ 该副本正被**当前孩子自己**的有效预约锁着 → 核销预约并借出（"自己预约的可以直接借"）
          ⑤ 有在馆副本 → 走标准借书链（会员/押金/额度/同书未还/AR 提示全量校验，人工放行口径不变）
          ⑥ 其余 → 按原因拦截：别人借出 / 别人预约锁定 / 维护 / 遗失 / 无在馆副本

        为什么不把 ④ 塞进 `borrow()`：reserved 副本"只有预约核销能动"是一条不变量，
        扫码端点负责**分流**到既有 checkout，而不是放宽 borrow 的守卫（红线 R2）。
        """
        from backend.domain.identity.member_code import is_valid_member_code, normalize_member_code
        from backend.domain.reading.models import Reservation

        raw = normalize_member_code(code)
        if is_valid_member_code(raw):
            target = self.circ.child_id_by_member_code(raw)
            return {
                "action": "member",
                "message": "已识别会员码",
                "card": self.circ.child_card(target),
            }
        # 形似会员码但校验位不过 → 明确说"码不合法"，别让他看到"ISBN xxx 未入库"这种误导文案
        # （实测：手输错一位扫进来会落到 ISBN 分支，馆员会以为是书没建档）
        if raw.startswith("M") and len(raw) == 10:
            raise ValidationError("会员码不合法（校验位不对：手输错位或码被改过，请重新扫）")

        isbn = (code or "").strip()
        if not isbn:
            raise ValidationError("请扫码：会员码或图书 ISBN")
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        book = self.db.query(Book).filter(Book.isbn == isbn, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError(f"ISBN {isbn} 未入库（这本书的条码还没录入？先到图书管理建档）")

        now = datetime.now()
        # ③ 还书优先：同一本书孩子只可能有一本在手上（同书未还禁借，见 borrow 第 2 步）
        active = (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.book_id == book.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.id)
            .first()
        )
        if active:
            record = self.circ.return_book(admin, active.copy_id, "normal")
            return {
                "action": "return",
                "message": f"已还《{book.title}》（正常归架）",
                "book_title": book.title,
                "copy_id": active.copy_id,
                "due_at": record.due_at,
                "record": record,
                "warnings": [],
            }

        # ④ 自己预约的副本 → 核销（唯一把 reserved 副本借出的正当路径）
        own_res = (
            self.db.query(Reservation)
            .filter(
                Reservation.child_id == child_id,
                Reservation.book_id == book.id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
                Reservation.expires_at >= now,
            )
            .order_by(Reservation.id)
            .first()
        )
        if own_res:
            from backend.domain.reading.service import ReservationAdminService

            record, _res = ReservationAdminService(self.db).checkout(admin, own_res.id)
            return {
                "action": "checkout",
                "message": f"已核销预约并借出《{book.title}》",
                "book_title": book.title,
                "copy_id": own_res.copy_id,
                "due_at": record.due_at,
                "record": record,
                "warnings": [],
            }

        # ⑤ 在馆副本 → 标准借书链
        copy = (
            self.db.query(BookCopy)
            .filter(
                BookCopy.book_id == book.id,
                BookCopy.status == BookCopy.STATUS_AVAILABLE,
                BookCopy.is_deleted == 0,
            )
            .order_by(BookCopy.id)
            .first()
        )
        if copy:
            record, warnings = self.circ.borrow(admin, child_id, copy.id, None, None)
            return {
                "action": "borrow",
                "message": f"已借出《{book.title}》",
                "book_title": book.title,
                "copy_id": copy.id,
                "due_at": record.due_at,
                "record": record,
                "warnings": warnings,
            }

        # ⑥ 拦截：把"为什么不能动"说清楚（馆员要靠这句话决定下一步）
        copies = (
            self.db.query(BookCopy)
            .filter(BookCopy.book_id == book.id, BookCopy.is_deleted == 0)
            .all()
        )
        if not copies:
            raise ConflictError(f"《{book.title}》没有可借副本（图书档案里未建副本）")
        holder = (
            self.db.query(BorrowRecord, Child)
            .join(Child, Child.id == BorrowRecord.child_id)
            .filter(
                BorrowRecord.book_id == book.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.id)
            .first()
        )
        if holder:
            rec, owner = holder
            when = (
                f"已逾期（应还 {rec.due_at:%Y-%m-%d}）"
                if rec.status == BorrowRecord.STATUS_OVERDUE
                else f"应还 {rec.due_at:%Y-%m-%d}"
            )
            raise ConflictError(
                f"《{book.title}》已被 {owner.name} 借出（{when}），不能借给 {child.name}"
            )
        locked = (
            self.db.query(Reservation, Child)
            .join(Child, Child.id == Reservation.child_id)
            .filter(
                Reservation.book_id == book.id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.expires_at >= now,
                Reservation.is_deleted == 0,
            )
            .order_by(Reservation.id)
            .first()
        )
        if locked:
            res, owner = locked
            raise ConflictError(
                f"《{book.title}》已被 {owner.name} 预约锁定（保留至 {res.expires_at:%m-%d %H:%M}），不能借出"
            )
        status_zh = "、".join(
            {
                BookCopy.STATUS_RESERVED: "预约锁定",
                BookCopy.STATUS_BORROWED: "已借出",
                BookCopy.STATUS_MAINTENANCE: "维护中",
                BookCopy.STATUS_LOST: "遗失",
            }.get(c.status, c.status)
            for c in copies
        )
        raise ConflictError(f"《{book.title}》当前不可借：{status_zh}")
