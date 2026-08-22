# backend/domain/reading/service.py — 完播判定（区间并集）/ 打卡 / 预约
"""防刷核心（红线：音频完播防刷）：
- 区间并集合并（重复段只计一次，seek 跳过不计）
- 覆盖增速校验（PRD R-151）：Δ覆盖 ≤ 服务端时间差 × 2.0（最大倍速）× 1.2（容差）+ 宽限
  服务端时间差 = 距上次上报的墙上时钟（声明跨度可伪造，不作依据）
- 完播 = 覆盖/总时长 ≥ 95%（阈值进配置）
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import CheckIn, ReadingProgress, Reservation

MAX_SPEED = 2.0  # PRD D20：倍速五档上限
SPEED_TOLERANCE = 1.2  # R-151：容差
REPORT_GRACE_SECONDS = 60  # 首次上报/网络抖动宽限（心跳 10s 的理论上限为 10×2.0×1.2=24s）


def merge_intervals(intervals: list[list[int]], new: list[int]) -> list[list[int]]:
    """合并区间并集（排序 + 线性合并）。"""
    all_intervals = sorted([list(i) for i in intervals if i[1] > i[0]] + [new], key=lambda x: x[0])
    merged: list[list[int]] = []
    for start, end in all_intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def coverage_of(intervals: list[list[int]]) -> int:
    return sum(e - s for s, e in intervals)


class ReadingService:
    def __init__(self, db: Session):
        self.db = db

    def report_progress(
        self, child: Child, book_id: int, position: int, session_start: int | None = None
    ) -> dict:
        """小程序播放心跳上报。

        position: 当前播放位置（秒）；session_start: 本次会话起始位置（用于构造区间）。
        服务端记录 [session_start, position] 区间（含 seek 段由客户端如实上报）。
        """
        book = (
            self.db.query(Book)
            .filter(Book.id == book_id, Book.is_deleted == 0, Book.status == Book.STATUS_ON)
            .first()
        )
        if not book:
            raise NotFoundError("图书不存在或已下架")
        if not book.audio_path or not book.audio_duration_seconds:
            raise ValidationError("该书暂无音频")
        # 会员权限（FEAT-038：有效会员全馆在架；过期仅在手；未入会/退会无）
        if not child.is_active_member:
            if child.member_status == Child.MEMBER_EXPIRED:
                holding = (
                    self.db.query(func.count(BorrowRecord.id))
                    .filter(
                        BorrowRecord.child_id == child.id,
                        BorrowRecord.book_id == book_id,
                        BorrowRecord.status.in_(
                            [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
                        ),
                        BorrowRecord.is_deleted == 0,
                    )
                    .scalar()
                )
                if not holding:
                    raise ValidationError("会员已过期，只能收听手中在借的图书")
            else:
                raise ValidationError("需入会后才能收听（请到店咨询）")
        total = book.audio_duration_seconds
        if position < 0 or position > total + 5:
            raise ValidationError(f"播放位置异常（0-{total}）")

        progress = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        if not progress:
            progress = ReadingProgress(child_id=child.id, book_id=book_id, total_seconds=total)
            self.db.add(progress)
            self.db.flush()

        # 构造本次区间
        start = (
            session_start
            if session_start is not None and 0 <= session_start <= total
            else max(0, position - 30)
        )
        end = min(position, total)
        if end <= start:
            return self._progress_view(progress)

        # ---- 防刷：覆盖增速校验（R-151）----
        now = datetime.now()
        intervals = json.loads(progress.intervals or "[]")
        old_cov = progress.coverage_seconds
        merged = merge_intervals(intervals, [start, end])
        new_cov = coverage_of(merged)
        delta_cov = new_cov - old_cov
        # 服务端时间差 = 距上次上报的墙上时钟秒数；声明跨度可伪造，不作依据
        elapsed = (
            (now - progress.last_report_at).total_seconds() if progress.last_report_at else 0.0
        )
        allowed = elapsed * MAX_SPEED * SPEED_TOLERANCE + REPORT_GRACE_SECONDS
        if delta_cov > allowed:
            raise ValidationError("播放数据异常（覆盖增速超过物理可能），本次上报被拒绝")

        just_finished = False
        if progress.finished == 0:
            threshold = int(ConfigService(self.db).get_value("audio_finish_threshold_percent"))
            if total > 0 and new_cov * 100 >= total * threshold:
                progress.finished = 1
                progress.finished_at = datetime.now()
                progress.reading_minutes = max(1, total // 60)
                just_finished = True

        progress.intervals = json.dumps(merged)
        progress.coverage_seconds = new_cov
        progress.last_position = position
        progress.last_report_at = now
        self.db.flush()

        result = self._progress_view(progress)
        result["just_finished"] = just_finished

        if just_finished:
            checkin = self._checkin(child, book_id)
            result["checkin"] = checkin
        self.db.commit()
        return result

    def _checkin(self, child: Child, book_id: int) -> dict:
        """当天首次完播 → 打卡（同天幂等；连续天数计算）。"""
        today = date.today()
        exists = (
            self.db.query(func.count(CheckIn.id))
            .filter(
                CheckIn.child_id == child.id, CheckIn.checkin_date == today, CheckIn.is_deleted == 0
            )
            .scalar()
        )
        if exists:
            return {"checked_in": False, "reason": "今日已打卡"}
        yesterday = (
            self.db.query(CheckIn)
            .filter(
                CheckIn.child_id == child.id,
                CheckIn.checkin_date == today - timedelta(days=1),
                CheckIn.is_deleted == 0,
            )
            .first()
        )
        streak = (yesterday.streak + 1) if yesterday else 1
        self.db.add(CheckIn(child_id=child.id, checkin_date=today, book_id=book_id, streak=streak))
        self.db.flush()
        # 打卡事件 → growth 域发周期积分（同事务；无订阅者时为 no-op）
        from backend.common.events import CheckInEvent, event_bus

        event_bus.publish(CheckInEvent(child_id=child.id, streak_days=streak), db=self.db)
        return {"checked_in": True, "streak": streak, "date": str(today)}

    def _progress_view(self, p: ReadingProgress) -> dict:
        return {
            "coverage_seconds": p.coverage_seconds,
            "total_seconds": p.total_seconds,
            "coverage_percent": round(p.coverage_seconds * 100 / p.total_seconds, 1)
            if p.total_seconds
            else 0,
            "finished": bool(p.finished),
            "last_position": p.last_position,
            "reading_minutes": p.reading_minutes,
        }

    def get_progress(self, child: Child, book_id: int) -> dict:
        p = (
            self.db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child.id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        return (
            self._progress_view(p)
            if p
            else {
                "coverage_seconds": 0,
                "total_seconds": 0,
                "coverage_percent": 0,
                "finished": False,
                "last_position": 0,
                "reading_minutes": 0,
            }
        )

    def checkin_calendar(self, child: Child, days: int = 30) -> dict:
        rows = (
            self.db.query(CheckIn)
            .filter(CheckIn.child_id == child.id, CheckIn.is_deleted == 0)
            .order_by(CheckIn.checkin_date.desc())
            .limit(days)
            .all()
        )
        today_row = next((r for r in rows if r.checkin_date == date.today()), None)
        return {
            "dates": [str(r.checkin_date) for r in rows],
            "today_checked": bool(today_row),
            "current_streak": today_row.streak if today_row else 0,
        }


class ReservationService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, child: Child, book_id: int) -> Reservation:
        # 校验：有效会员 + 押金 + 无逾期 + 额度（在借+预约 ≤ 上限）
        if not child.is_active_member:
            raise ValidationError("仅有效会员可预约")
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .first()
        )
        if not dep or dep.status == "unpaid":
            raise ValidationError("押金未缴纳，不能预约")
        now = datetime.now()
        overdue = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.due_at < now,
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        if overdue:
            raise ValidationError("有逾期未还图书，请先归还")
        # 同书进行中预约唯一
        dup = (
            self.db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == child.id,
                Reservation.book_id == book_id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("该书已有进行中的预约")
        # 额度
        borrow_limit = int(ConfigService(self.db).get_value("borrow_limit"))
        active_borrows = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        active_reservations = (
            self.db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == child.id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .scalar()
        )
        if active_borrows + active_reservations >= borrow_limit:
            raise ValidationError(
                f"借阅额度已满（在借 {active_borrows} + 预约 {active_reservations} / 上限 {borrow_limit}）"
            )

        # 锁副本
        copy = (
            self.db.query(BookCopy)
            .filter(
                BookCopy.book_id == book_id,
                BookCopy.status == BookCopy.STATUS_AVAILABLE,
                BookCopy.is_deleted == 0,
            )
            .order_by(BookCopy.id)
            .with_for_update()
            .first()
        )
        if not copy:
            raise ConflictError("该书当前无在馆副本可预约")
        hours = int(ConfigService(self.db).get_value("reservation_hours"))
        res = Reservation(
            child_id=child.id,
            book_id=book_id,
            copy_id=copy.id,
            expires_at=now + timedelta(hours=hours),
            status=Reservation.STATUS_ACTIVE,
        )
        copy.status = BookCopy.STATUS_RESERVED
        self.db.add(res)
        self.db.flush()
        self.db.commit()
        return res

    def cancel(self, child: Child, reservation_id: int) -> Reservation:
        res = (
            self.db.query(Reservation)
            .filter(
                Reservation.id == reservation_id,
                Reservation.child_id == child.id,
                Reservation.is_deleted == 0,
            )
            .first()
        )
        if not res or res.status != Reservation.STATUS_ACTIVE:
            raise ValidationError("预约不存在或状态不可取消")
        res.status = Reservation.STATUS_CANCELLED
        copy = self.db.query(BookCopy).filter(BookCopy.id == res.copy_id).first()
        if copy and copy.status == BookCopy.STATUS_RESERVED:
            copy.status = BookCopy.STATUS_AVAILABLE
        self.db.commit()
        return res

    def list_mine(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Reservation, Book)
            .join(Book, Reservation.book_id == Book.id)
            .filter(Reservation.child_id == child.id, Reservation.is_deleted == 0)
            .order_by(Reservation.id.desc())
            .all()
        )
        out = []
        for res, book in rows:
            out.append(
                {
                    "id": res.id,
                    "book_id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "status": res.status,
                    "expires_at": str(res.expires_at),
                }
            )
        return out


class ReservationAdminService:
    """管理端预约管理/核销 + 孩子阅读档案（WM6 手册步骤 12-14）。"""

    def __init__(self, db: Session):
        self.db = db

    def list_reservations(self, status: str | None = None) -> list[dict]:
        """预约管理列表（默认全部；status=active 看锁定中）。"""
        q = (
            self.db.query(Reservation, Child, Parent)
            .join(Child, Reservation.child_id == Child.id)
            .join(Parent, Child.parent_id == Parent.id)
            .filter(Reservation.is_deleted == 0)
        )
        if status:
            q = q.filter(Reservation.status == status)
        rows = q.order_by(Reservation.id.desc()).limit(200).all()
        book_ids = {r[0].book_id for r in rows}
        books = (
            {b.id: b for b in self.db.query(Book).filter(Book.id.in_(book_ids)).all()}
            if book_ids
            else {}
        )
        now = datetime.now()
        return [
            {
                "id": res.id,
                "child_id": child.id,
                "child_name": child.name,
                "parent_name": parent.name,
                "parent_phone": parent.phone,
                "book_id": res.book_id,
                "book_title": books[res.book_id].title
                if res.book_id in books
                else f"#{res.book_id}",
                "copy_id": res.copy_id,
                "status": res.status,
                "created_at": res.create_time,
                "expires_at": res.expires_at,
                "expired": res.status == Reservation.STATUS_ACTIVE and res.expires_at < now,
            }
            for res, child, parent in rows
        ]

    def child_reading_profile(self, child_id: int) -> dict:
        """孩子档案的阅读数据（完播书单/打卡/时长）。"""
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        progresses = (
            self.db.query(ReadingProgress, Book)
            .join(Book, ReadingProgress.book_id == Book.id)
            .filter(
                ReadingProgress.child_id == child_id,
                ReadingProgress.finished == 1,
                ReadingProgress.is_deleted == 0,
            )
            .order_by(ReadingProgress.finished_at.desc())
            .all()
        )
        checkins = (
            self.db.query(CheckIn)
            .filter(CheckIn.child_id == child_id, CheckIn.is_deleted == 0)
            .order_by(CheckIn.checkin_date.desc())
            .all()
        )
        today_row = next((r for r in checkins if r.checkin_date == date.today()), None)
        return {
            "child_id": child.id,
            "child_name": child.name,
            "member_status": child.member_status,
            "total_finished": len(progresses),
            "total_reading_minutes": sum(p.reading_minutes for p, _ in progresses),
            "total_checkin_days": len(checkins),
            "current_streak": today_row.streak if today_row else 0,
            "finished_books": [
                {
                    "book_id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "word_count": book.word_count,
                    "finished_at": p.finished_at,
                    "reading_minutes": p.reading_minutes,
                }
                for p, book in progresses
            ],
        }

    def checkout(self, admin, reservation_id: int):
        """核销预约 → 标准借书链（额度/押金/同书未还全量校验后借出锁定的副本）。"""
        res = (
            self.db.query(Reservation)
            .filter(Reservation.id == reservation_id, Reservation.is_deleted == 0)
            .with_for_update()
            .first()
        )
        if not res or res.status != Reservation.STATUS_ACTIVE:
            raise ValidationError("预约不存在或状态不可核销")
        if res.expires_at < datetime.now():
            raise ValidationError("预约已超时，请先走逾期释放")
        copy = self.db.query(BookCopy).filter(BookCopy.id == res.copy_id).with_for_update().first()
        if not copy or copy.status != BookCopy.STATUS_RESERVED:
            raise ConflictError("预约副本状态异常（可能已被释放）")
        # 副本 reserved→available 后走标准借书（borrow 内部 commit，一并提交预约状态）
        copy.status = BookCopy.STATUS_AVAILABLE
        res.status = Reservation.STATUS_CHECKED_OUT
        self.db.flush()
        from backend.domain.circulation.service import CirculationService

        record = CirculationService(self.db).borrow(
            admin, res.child_id, copy_id=res.copy_id, isbn=None
        )
        return record, res
