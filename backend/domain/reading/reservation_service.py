# backend/domain/reading/reservation_service.py — 预约（家长端 + 管理端/核销/善后）
"""从 service.py 拆出（god file 800 行限制，2026-10-22 第一百一十六批：打卡日历补区间后触线）。

引用路径保持：`router` / `miniapp_router` / `tasks.registry` / 测试均经 service.py
re-export，既有 import 写法不变。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.file_utils import book_cover_url
from backend.common.gateways.payment.types import yuan_to_cents
from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_RESERVATION_EXPIRING,
    NotificationService,
)
from backend.common.sql_utils import escape_like
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.borrow_gate import deposit_block_reason
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent
from backend.domain.reading.models import CheckIn, ReadingProgress, Reservation


class ReservationService:
    def __init__(self, db: Session):
        self.db = db

    #: 「我的预约」含历史时的条数上限（P2-44 同款：上限经响应头如实告知）
    HISTORY_LIMIT = 50

    def create(self, child: Child, book_id: int) -> Reservation:
        # 锁主体行：同一孩子的并发预约串行化（E-7 提前 20260903，与 borrow/checkout
        # 同款锁序 Child 最先；Reservation 无唯一索引兜底，RC 不能替代行锁）
        child = (
            self.db.query(Child)
            .filter(Child.id == child.id, Child.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not child:
            raise NotFoundError("孩子不存在")
        # 校验：有效会员 + 押金 + 无逾期 + 额度（在借+预约 ≤ 上限）
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，预约已冻结")
        if not child.is_active_member:
            raise ValidationError("仅有效会员可预约")
        # 2026-10-12 拍板（P6）：**下架/已删除的书禁预约**（PRD §4.4 既有口径，代码此前没做）
        from backend.domain.catalog.models import Book

        book = self.db.query(Book).filter(Book.id == book_id, Book.is_deleted == 0).first()
        if book is None:
            raise NotFoundError("书目不存在")
        if book.status != Book.STATUS_ON:
            raise ValidationError(f"《{book.title}》已下架，不能预约")
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .first()
        )
        # B-12（第二批 T12）：押金扣光/有未结清赔偿款与未缴纳同险；预约为线上自助操作，
        # 硬拦截无人工放行（宪法三类硬拦截例外）。
        # **第十轮 W1-1（P2，2026-10-25）**：判据改为调用借书闸的**同一份**
        # `borrow_gate.deposit_block_reason`（白名单：`paid`/`partially_deducted` 且无欠款才放行）。
        # 原实现在这里**又写了一遍**黑名单，漏掉 `refunding`/`refunded`：
        # 押金退款在途或已退未复位的孩子仍能自助预约并锁住副本 72h，到店核销时才被
        # `deposit_gate` 拦下（家长白约、别的孩子借不到）。同一判定写两遍必然漂移。
        block_reason = deposit_block_reason(
            deposit_status=dep.status if dep else None,
            deposit_unpaid_balance=(
                yuan_to_cents(dep.unpaid_balance or Decimal("0")) if dep else 0
            ),
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
        )
        if block_reason:
            raise ValidationError(f"{block_reason}，不能预约")
        now = datetime.now()
        # 2026-09-21 甲方口径：逾期不拦截（全端统一）——原先此处对"有逾期未还"硬拦
        # （raise "有逾期未还图书，请先归还"）已按裁定移除；逾期只占借阅额度里的一个名额
        # （见 PRD §5.4 现行口径与 CLAUDE.md §二 借阅并发红线）。
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
            .populate_existing()
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
        # 第九轮 P1-1（2026-10-22）：**预约成功事件此前全仓没有发布方**——订阅器
        # （`tasks/notify_handlers._on_reservation_created`）与微信模板映射三件都在，
        # 唯独缺这一行：家长预约成功后永远收不到「预约成功」站内信，而接口照常 200、
        # 无审计、无死信（最典型的静默数据缺失）。同事务发布，订阅器抛错即整体回滚。
        from backend.common.events import ReservationCreatedEvent, event_bus

        event_bus.publish(
            ReservationCreatedEvent(child_id=child.id, book_id=book_id, reservation_id=res.id),
            db=self.db,
        )
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
            .with_for_update()
            .populate_existing()  # P1-F4：锁定读（锁序 Reservation → copy，与核销一致）
            .first()
        )
        if not res or res.status != Reservation.STATUS_ACTIVE:
            raise ValidationError("预约不存在或状态不可取消")
        res.status = Reservation.STATUS_CANCELLED
        copy = (
            self.db.query(BookCopy)
            .filter(BookCopy.id == res.copy_id)
            .with_for_update()
            .populate_existing()
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
                .populate_existing()
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

    def list_mine(self, child: Child, include_closed: bool = False) -> list[dict]:
        """家长视角预约列表。

        `include_closed=False`（默认，书架/首页/详情页用的口径）：**只回有效锁定态**——
        核销(checked_out)/取消(cancelled)/超时(expired)/异常(exception) 不进「预约中」计数。

        `include_closed=True`（「我的预约」页专用）：连**已结束**的单子一起回，按 id 倒序取
        `HISTORY_LIMIT` 条。为什么必须有（第八轮 P3 / docs/04 S35）：`docs/04` 的验收点写的是
        「家长端我的预约**显示已核销**」，而原实现只有 active 一支 → 页面里
        `checked_out/expired/cancelled` 三个徽标是**永远不会出现的死代码**，家长核销后
        预约**直接从列表消失**（"记录丢了"的观感）。默认值不变 ⇒ 既有三个消费端行为零变化。
        """
        q = (
            self.db.query(Reservation, Book)
            .join(Book, Reservation.book_id == Book.id)
            .filter(
                Reservation.child_id == child.id,
                Reservation.is_deleted == 0,
            )
        )
        if include_closed:
            q = q.order_by(Reservation.id.desc()).limit(self.HISTORY_LIMIT)
        else:
            # 书架"预约中"只显示有效锁定态（见 docstring）
            q = q.filter(Reservation.status == Reservation.STATUS_ACTIVE).order_by(
                Reservation.id.desc()
            )
        out = []
        for res, book in q.all():
            out.append(
                {
                    "id": res.id,
                    "book_id": book.id,
                    "title": book.title,
                    "author": book.author,
                    "cover_url": book_cover_url(book.id, book.cover_path),
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

    #: 管理端预约列表硬上限（P2-44：上限与是否已达上限经响应头告知）
    ADMIN_LIST_LIMIT = 200

    def list_reservations(
        self, status: str | None = None, keyword: str | None = None
    ) -> list[dict]:
        """预约管理列表（默认全部；status=active 看锁定中；keyword 模糊匹配
        孩子名/家长名/家长手机号——B7，订单 keyword A5 先例同款）。"""
        q = (
            self.db.query(Reservation, Child, Parent)
            .join(Child, Reservation.child_id == Child.id)
            .join(Parent, Child.parent_id == Parent.id)
            .filter(Reservation.is_deleted == 0)
        )
        if status:
            q = q.filter(Reservation.status == status)
        if keyword and keyword.strip():
            kw = f"%{escape_like(keyword.strip())}%"
            q = q.filter(
                or_(
                    Child.name.ilike(kw, escape="\\"),
                    Parent.name.ilike(kw, escape="\\"),
                    Parent.phone.ilike(kw, escape="\\"),
                )
            )
        rows = q.order_by(Reservation.id.desc()).limit(self.ADMIN_LIST_LIMIT).all()
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
            self.db.query(ChildModel).filter(
                ChildModel.id == _res_pre.child_id
            ).with_for_update().populate_existing().first()
        res = (
            self.db.query(Reservation)
            .filter(Reservation.id == reservation_id, Reservation.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # 顺带-1：_res_pre 已载 identity map，锁定读不刷新则守卫读旧值（E-01 防复发第 2 条）
            .first()
        )
        if not res or res.status != Reservation.STATUS_ACTIVE:
            raise ValidationError("预约不存在或状态不可核销")
        if res.expires_at < datetime.now():
            raise ValidationError("预约已超时，请先走逾期释放")
        copy = (
            self.db.query(BookCopy)
            .filter(BookCopy.id == res.copy_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
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
