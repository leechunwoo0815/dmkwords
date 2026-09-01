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
from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_RESERVATION_EXPIRING,
    NotificationService,
)
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import (
    CheckIn,
    DictionaryWord,
    Favorite,
    ReadingProgress,
    Reservation,
    Vocabulary,
)

MAX_SPEED = 2.0  # PRD D20：倍速五档上限
SPEED_TOLERANCE = 1.2  # R-151：容差
REPORT_GRACE_SECONDS = 60  # 首次上报/网络抖动宽限（心跳 10s 的理论上限为 10×2.0×1.2=24s）


def _clean_intervals(raw: object) -> list[list[int]]:
    """清洗历史 intervals：丢弃非法区间（非 [a,b]、含非数字、a>=b、负起点），
    保证 merge/coverage 对脏数据永不 500（E-20260830-12 防御）。"""
    if not isinstance(raw, list):
        return []
    cleaned: list[list[int]] = []
    for item in raw:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            start, end = int(item[0]), int(item[1])
            if 0 <= start < end:
                cleaned.append([start, end])
    return cleaned


def merge_intervals(intervals: object, new: list[int]) -> list[list[int]]:
    """合并区间并集（排序 + 线性合并）。intervals 为脏数据时清洗后合并，不抛异常。"""
    all_intervals = sorted(_clean_intervals(intervals) + [list(new)], key=lambda x: x[0])
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
            if child.is_expired_member:
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
        try:
            intervals = json.loads(progress.intervals or "[]")
        except (json.JSONDecodeError, TypeError):
            intervals = []  # E-20260830-12：字段损坏时按空区间处理，不 500
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
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，预约已冻结")
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
            .with_for_update()  # P1-F4：锁定读（锁序 Reservation → copy，与核销一致）
            .first()
        )
        if not res or res.status != Reservation.STATUS_ACTIVE:
            raise ValidationError("预约不存在或状态不可取消")
        res.status = Reservation.STATUS_CANCELLED
        copy = (
            self.db.query(BookCopy)
            .filter(BookCopy.id == res.copy_id)
            .with_for_update()
            .first()
        )
        if copy and copy.status == BookCopy.STATUS_RESERVED:  # 锁后当前读
            copy.status = BookCopy.STATUS_AVAILABLE
        self.db.commit()
        return res

    def expire_due(self) -> int:
        """预约超时释放（FEAT-019/PRD §4）：expired + 副本回 available + 通知家长。幂等。"""
        from backend.common.events import ReservationExpiredEvent, event_bus

        now = datetime.now()
        due = (
            self.db.query(Reservation)
            .filter(
                Reservation.is_deleted == 0,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.expires_at < now,
            )
            .all()
        )
        for item in due:
            # P1-F4：逐条锁定读（锁序 Reservation → copy，与核销/取消一致）+
            # 状态守卫——已被并发核销（checked_out）的预约跳过，防把已借出副本改回 available
            res = (
                self.db.query(Reservation)
                .filter(Reservation.id == item.id)
                .with_for_update()
                .populate_existing()  # 强制用行数据刷新 identity map——due 扫描已按
                # 旧快照加载过同一行，不刷新则状态守卫读到旧值失效（并发核销漏拦）
                .first()
            )
            if not res or res.status != Reservation.STATUS_ACTIVE:
                continue
            res.status = Reservation.STATUS_EXPIRED
            copy = (
                self.db.query(BookCopy)
                .filter(BookCopy.id == res.copy_id)
                .with_for_update()
                .first()
            )
            if copy and copy.status == BookCopy.STATUS_RESERVED:  # 锁后当前读
                copy.status = BookCopy.STATUS_AVAILABLE
            event_bus.publish(
                ReservationExpiredEvent(
                    child_id=res.child_id,
                    book_id=res.book_id,
                    reservation_id=res.id,
                ),
                db=self.db,
            )
        if due:
            self.db.commit()
        return len(due)  # 扫描数（跳过的并发核销单不在内，幂等语义不变）

    def expire_remind(self) -> int:
        """预约即将到期提醒（距 expires_at ≤ remind_hours 且未过；每次预约一条）。"""
        from backend.common.config_service import ConfigService

        hours = int(ConfigService(self.db).get_value("reservation_remind_hours", "24"))
        now = datetime.now()
        window_end = now + timedelta(hours=hours)
        upcoming = (
            self.db.query(Reservation)
            .filter(
                Reservation.is_deleted == 0,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.expires_at > now,
                Reservation.expires_at <= window_end,
            )
            .all()
        )
        sent = 0
        for res in upcoming:
            child = self.db.query(Child).filter(Child.id == res.child_id).first()
            if not child:
                continue
            book = self.db.query(Book).filter(Book.id == res.book_id).first()
            title = book.title if book else f"书目#{res.book_id}"
            parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
            if NotificationService(self.db).send(
                parent_id=child.parent_id,
                scene=SCENE_RESERVATION_EXPIRING,
                title="预约即将到期",
                content=(
                    f"《{title}》的预约将于 {res.expires_at:%Y-%m-%d %H:%M} 到期，"
                    f"请尽快到馆取书，超时自动释放。"
                ),
                category=Notification.CATEGORY_RESERVATION,
                child_id=child.id,
                ref_type="reservation",
                ref_id=str(res.id),
                openid=parent.wechat_openid if parent else None,
            ):
                sent += 1
        if sent:
            self.db.commit()
        return sent

    def list_mine(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Reservation, Book)
            .join(Book, Reservation.book_id == Book.id)
            .filter(
                Reservation.child_id == child.id,
                # 书架"预约中"只显示有效锁定态：核销(checked_out)/取消(cancelled)/
                # 超时(expired)/异常(exception) 均不应再出现
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
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
                    "cover_url": f"/api/miniapp/covers/{book.id}" if book.cover_path else None,
                    "has_audio": bool(book.audio_path),
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
        # P1-F8：全局锁序统一 Child 最先（与 borrow 一致），消除 checkout 持 copy 锁
        # 进 borrow 等 Child 锁的 AB-BA 死锁窗口（本函数 Child 锁后由 borrow 重入）
        from backend.domain.identity.models import Child as ChildModel
        from backend.domain.reading.models import Reservation as _Res

        _res_pre = (
            self.db.query(_Res).filter(_Res.id == reservation_id, _Res.is_deleted == 0).first()
        )
        if _res_pre:
            self.db.query(ChildModel).filter(ChildModel.id == _res_pre.child_id).with_for_update().first()
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

        record, _borrow_warnings = CirculationService(self.db).borrow(
            admin, res.child_id, copy_id=res.copy_id, isbn=None
        )
        return record, res


class VocabularyService:
    """查词 + 生词本（FEAT-054/055：主动查词自动收录；同词唯一；记来源书）。"""

    def __init__(self, db: Session):
        self.db = db

    def lookup(self, child: Child, word: str, book_id: int | None = None) -> dict:
        w = (word or "").strip().lower()
        if not w or not w.isascii() or not w.isalpha() or len(w) > 64:
            raise ValidationError("请输入一个英文单词（仅字母）")
        entry = (
            self.db.query(DictionaryWord)
            .filter(DictionaryWord.word == w, DictionaryWord.is_deleted == 0)
            .first()
        )
        if not entry:
            raise NotFoundError(f"词库里没有「{w}」（第一期支持精确查询）")
        # 自动收录（同词唯一；重复查更新来源书记录但不重复）
        # 含软删行一起查：唯一索引 uq_vocab_child_word 不含 is_deleted，
        # 软删行会挡住重新 INSERT（C50：删词后再查同词曾 500）
        existing = (
            self.db.query(Vocabulary)
            .filter(Vocabulary.child_id == child.id, Vocabulary.word == w)
            .first()
        )
        recorded = False
        if not existing:
            self.db.add(Vocabulary(child_id=child.id, word=w, book_id=book_id))
            recorded = True
        else:
            if existing.is_deleted:  # 删除后再查 → 复活收录
                existing.is_deleted = 0
                recorded = True
            if book_id and not existing.book_id:
                existing.book_id = book_id
        self.db.commit()
        return {
            "word": entry.word,
            "phonetic": entry.phonetic,
            "definition": entry.definition,
            "translation": entry.translation,
            "recorded": recorded,
        }

    def list_words(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Vocabulary)
            .filter(Vocabulary.child_id == child.id, Vocabulary.is_deleted == 0)
            .order_by(Vocabulary.id.desc())
            .all()
        )
        book_ids = {r.book_id for r in rows if r.book_id}
        books = (
            {b.id: b.title for b in self.db.query(Book).filter(Book.id.in_(book_ids)).all()}
            if book_ids
            else {}
        )
        return [
            {
                "id": r.id,
                "word": r.word,
                "book_id": r.book_id,
                "source_title": books.get(r.book_id, ""),
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    def remove(self, child: Child, vocabulary_id: int) -> None:
        row = (
            self.db.query(Vocabulary)
            .filter(
                Vocabulary.id == vocabulary_id,
                Vocabulary.child_id == child.id,
                Vocabulary.is_deleted == 0,
            )
            .first()
        )
        if not row:
            raise NotFoundError("生词不存在")
        row.is_deleted = 1
        self.db.commit()


class FavoriteService:
    """收藏夹（FEAT-056：想读清单；不限量不占额度；下架书可见标注）。"""

    def __init__(self, db: Session):
        self.db = db

    def list_mine(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(Favorite, Book)
            .join(Book, Favorite.book_id == Book.id)
            .filter(Favorite.child_id == child.id, Favorite.is_deleted == 0)
            .order_by(Favorite.id.desc())
            .all()
        )
        return [
            {
                "id": f.id,
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "word_count": b.word_count,
                "ar_level": b.ar_level,
                "cover_url": f"/api/miniapp/covers/{b.id}" if b.cover_path else None,
                "has_audio": bool(b.audio_path),
                "off_shelf": b.status != Book.STATUS_ON,
                "created_at": str(f.created_at),
            }
            for f, b in rows
        ]

    def add(self, child: Child, book_id: int) -> dict:
        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError("图书不存在")
        dup = (
            self.db.query(func.count(Favorite.id))
            .filter(
                Favorite.child_id == child.id,
                Favorite.book_id == book_id,
                Favorite.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("已收藏过这本书")
        self.db.add(Favorite(child_id=child.id, book_id=book_id))
        self.db.commit()
        return {"book_id": book_id, "title": book.title}

    def remove(self, child: Child, book_id: int) -> None:
        row = (
            self.db.query(Favorite)
            .filter(
                Favorite.child_id == child.id,
                Favorite.book_id == book_id,
                Favorite.is_deleted == 0,
            )
            .first()
        )
        if not row:
            raise NotFoundError("未收藏该书")
        row.is_deleted = 1
        self.db.commit()


class ShelfService:
    """书架：当前在借（借书自动上架、还书自动下架）。"""

    def __init__(self, db: Session):
        self.db = db

    def current_borrows(self, child: Child) -> list[dict]:
        now = datetime.now()
        rows = (
            self.db.query(BorrowRecord, Book)
            .join(Book, BorrowRecord.book_id == Book.id)
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.due_at)
            .all()
        )
        return [
            {
                "record_id": r.id,
                "book_id": b.id,
                "title": b.title,
                "author": b.author,
                "word_count": b.word_count,
                "cover_url": f"/api/miniapp/covers/{b.id}" if b.cover_path else None,
                "has_audio": bool(b.audio_path),
                "borrowed_at": str(r.borrowed_at),
                "due_at": str(r.due_at),
                "overdue": r.due_at < now,
            }
            for r, b in rows
        ]
