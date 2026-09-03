# backend/domain/circulation/service.py — 借/还/续借/逾期扣减/人工放行
"""并发纪律（模式手册 P10/P11）：锁主体行（Child with_for_update）串行化同一孩子的借书；
副本行锁 + 唯一索引双保险防同一副本并发借出。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.events import BookBorrowedEvent, BookReturnedEvent, event_bus
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_BORROW_DUE_REMIND,
    SCENE_BORROW_OVERDUE,
    NotificationService,
)
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.catalog.models import Book, BookCopy
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent


class CirculationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 查询 ----------
    def child_card(self, child_id: int) -> dict:
        """借阅操作台的孩子卡片（WM5 核心视图）。"""
        child = (
            self.db.query(Child, Parent)
            .join(Parent, Child.parent_id == Parent.id)
            .filter(Child.id == child_id, Child.is_deleted == 0)
            .first()
        )
        if not child:
            raise NotFoundError("孩子不存在")
        child, parent = child
        now = datetime.now()
        active = (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .all()
        )
        overdue = [r for r in active if r.due_at < now]
        borrow_limit = int(ConfigService(self.db).get_value("borrow_limit"))
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        return {
            "child": child,
            "parent": parent,
            "active_borrows": len(active),
            "overdue_count": len(overdue),
            "available_quota": max(0, borrow_limit - len(overdue) - len(active)),
            "borrow_limit": borrow_limit,
            "deposit_status": dep.status if dep else "unpaid",
            "deposit_available": str(dep.available_amount) if dep else "0",
            "active_records": active,
            "overdue_records": overdue,
        }

    def find_copy_by_isbn(self, isbn: str) -> tuple[Book, BookCopy]:
        """ISBN → 书目 + 一个在馆副本（多副本取第一个 available）。"""
        book = self.db.query(Book).filter(Book.isbn == isbn, Book.is_deleted == 0).first()
        if not book:
            raise NotFoundError(f"ISBN {isbn} 未入库")
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
        return book, copy

    # ---------- 借书 ----------
    def borrow(
        self,
        admin,
        child_id: int,
        copy_id: int | None,
        isbn: str | None,
        override_reason: str | None = None,
    ) -> tuple[BorrowRecord, list[str]]:
        # 锁主体行：同一孩子的并发借书串行化（模式手册 P10）
        child = self.db.query(Child).filter(Child.id == child_id).with_for_update().populate_existing().first()
        if not child:
            raise NotFoundError("孩子不存在")
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，借书已冻结")

        # 同书未还禁借（重复借阅精确判定需 book_id；isbn/copy 路径先解析书目）
        _dup_book_id = None
        if isbn:
            _b = self.db.query(Book).filter(Book.isbn == isbn, Book.is_deleted == 0).first()
            _dup_book_id = _b.id if _b else None
        elif copy_id:
            _c = self.db.query(BookCopy).filter(BookCopy.id == copy_id).first()
            _dup_book_id = _c.book_id if _c else None
        if _dup_book_id:
            _dup = (
                self.db.query(func.count(BorrowRecord.id))
                .filter(
                    BorrowRecord.child_id == child_id,
                    BorrowRecord.book_id == _dup_book_id,
                    BorrowRecord.status.in_(
                        [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
                    ),
                    BorrowRecord.is_deleted == 0,
                )
                .scalar()
            )
            if _dup:
                raise ConflictError("该书尚未归还，不能重复借阅")

        # 定位副本
        if copy_id:
            copy = self.db.query(BookCopy).filter(BookCopy.id == copy_id).with_for_update().populate_existing().first()
            if not copy:
                raise NotFoundError("副本不存在")
            book = self.db.query(Book).filter(Book.id == copy.book_id).first()
        elif isbn:
            book, copy = self.find_copy_by_isbn(isbn)
            if not copy:
                raise ConflictError(f"《{book.title}》当前无在馆副本")
            copy = self.db.query(BookCopy).filter(BookCopy.id == copy.id).with_for_update().populate_existing().first()
        else:
            raise ValidationError("请提供副本ID或ISBN")

        # ---- 校验链 ----
        warnings: list[str] = []
        unpaid_override = False  # 未入会放行借阅：72 小时借期（R-313）
        if not child.is_active_member:
            # R-313 借书矩阵行：未缴费=开关+放行+限 1 本；过期=软提示可放行；退会=禁
            if child.member_status == Child.MEMBER_WITHDRAWN:
                raise ValidationError("孩子已退会，禁止借书（R-313）")
            if child.member_status == Child.MEMBER_NONE:
                # 未入会：默认硬拦截；开关开启 + 放行原因才可借，且每次限 1 本（R-313/C15）
                allow = ConfigService(self.db).get_value("allow_unpaid_offline_borrow") == "true"
                if not allow or not override_reason:
                    raise ValidationError(
                        f"孩子会员状态为 {child.member_status}，"
                        + (
                            "未入会临时借书开关未开启"
                            if not allow
                            else "未入会借书需管理员放行并填写原因"
                        )
                    )
                held = (
                    self.db.query(func.count(BorrowRecord.id))
                    .filter(
                        BorrowRecord.child_id == child_id,
                        BorrowRecord.status.in_(
                            [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
                        ),
                        BorrowRecord.is_deleted == 0,
                    )
                    .scalar()
                )
                if held >= 1:
                    raise ValidationError(
                        f"未入会临时借书每次限 1 本（当前已借 {held} 本未还），"
                        "请先归还或办理入会（R-313）"
                    )
                unpaid_override = True
                warnings.append(f"未入会临时借书（原因：{override_reason}）：72 小时内归还或入会")
            else:
                # 过期（状态 expired 或 formal 已到期未落库）：软提示，馆员放行即可，不吃未入会开关（D3/C17）
                if not override_reason:
                    raise ValidationError("孩子会员已过期，需馆员放行并填写原因（可放行）")
                warnings.append(f"会员已过期，馆员放行借书（原因：{override_reason}）")

        # 押金校验
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        if not dep or dep.status == "unpaid":
            if not override_reason:
                raise ValidationError("押金未缴纳（可人工放行并填写原因）")
            warnings.append("押金未缴纳")

        # 借阅上限：30 − 逾期未还数（V1.1 §5.4）
        borrow_limit = int(ConfigService(self.db).get_value("borrow_limit"))
        now = datetime.now()
        active_count = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        overdue_count = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.due_at < now,
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        quota = borrow_limit - overdue_count - active_count
        if quota <= 0 and not override_reason:
            raise ValidationError(
                f"可借上限已满（上限 {borrow_limit}，逾期 {overdue_count} 本，在借 {active_count} 本）"
            )
        if quota <= 0:
            warnings.append(f"超上限放行（逾期 {overdue_count}，在借 {active_count}）")

        # 副本状态（C2：状态名中文化，禁止英文状态码泄漏给馆员）
        if copy.status != BookCopy.STATUS_AVAILABLE:
            status_zh = {
                BookCopy.STATUS_RESERVED: "预约锁定",
                BookCopy.STATUS_BORROWED: "已借出",
                BookCopy.STATUS_MAINTENANCE: "维护中",
                BookCopy.STATUS_LOST: "遗失",
            }.get(copy.status, copy.status)
            suffix = "（预约锁定请走预约核销）" if copy.status == BookCopy.STATUS_RESERVED else ""
            raise ConflictError(f"副本当前状态：{status_zh}，不可借出{suffix}")

        # AR 超范围软提示（FEAT-031：提示不拦截；阈值走配置 ar_warning_range）
        if child.ar_level and book.ar_level:
            try:
                ar_diff = abs(float(child.ar_level) - float(book.ar_level))
            except (TypeError, ValueError):
                ar_diff = None  # 无法解析的 AR 值不提示
            if ar_diff is not None:
                ar_range = float(ConfigService(self.db).get_value("ar_warning_range", "0.5"))
                if ar_diff > ar_range:
                    warnings.append(
                        f"AR 超范围提示：孩子 AR {child.ar_level}，本书 AR {book.ar_level}（不拦截，请确认）"
                    )

        # ---- 写入 ----
        borrow_days = int(ConfigService(self.db).get_value("borrow_days"))
        due_at = now + timedelta(days=borrow_days)
        if unpaid_override:
            due_at = now + timedelta(hours=72)  # R-313：未入会放行借阅 72 小时内归还或入会
        record = BorrowRecord(
            child_id=child_id,
            copy_id=copy.id,
            book_id=copy.book_id,
            borrowed_at=now,
            due_at=due_at,
            status=BorrowRecord.STATUS_ACTIVE,
            borrowed_by=admin.id,
            override_reason=override_reason,
        )
        self.db.add(record)
        copy.status = BookCopy.STATUS_BORROWED
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="circulation.borrow",
            target_type="borrow",
            target_id=str(record.id),
            detail={
                "child": child.name,
                "book": book.title,
                "copy": copy.copy_code,
                "warnings": warnings,
            },
            reason=override_reason or "正常借书",
        )
        event_bus.publish(
            BookBorrowedEvent(
                child_id=child_id,
                book_id=copy.book_id,
                book_copy_id=copy.id,
                borrow_record_id=record.id,
            ),
            db=self.db,
        )
        self.db.commit()
        return record, warnings

    # ---------- 还书 ----------
    def return_book(self, admin, copy_id: int, condition: str = "normal") -> BorrowRecord:
        """condition: normal / maintenance / lost（遗失联动押金赔偿提示）。"""
        if condition not in ("normal", "maintenance", "lost"):
            raise ValidationError("归还状态仅支持 normal/maintenance/lost")
        # P1-F5：锁定读（锁序 record → copy 全局统一）——并发双还时后到者
        # 阻塞后读到已 returned → NotFoundError，天然防重
        record = (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.copy_id == copy_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .with_for_update().populate_existing()
            .first()
        )
        if not record:
            raise NotFoundError("该副本没有进行中的借阅")
        copy = self.db.query(BookCopy).filter(BookCopy.id == copy_id).with_for_update().populate_existing().first()

        was_overdue = record.due_at < datetime.now()
        record.status = BorrowRecord.STATUS_RETURNED
        record.returned_at = datetime.now()
        record.returned_condition = condition
        if condition == "normal":
            copy.status = BookCopy.STATUS_AVAILABLE
        elif condition == "maintenance":
            copy.status = BookCopy.STATUS_MAINTENANCE
        else:  # lost
            record.status = BorrowRecord.STATUS_LOST
            copy.status = BookCopy.STATUS_LOST
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="circulation.return",
            target_type="borrow",
            target_id=str(record.id),
            detail={"condition": condition, "was_overdue": was_overdue},
            reason=f"还书（{condition}）",
        )
        event_bus.publish(
            BookReturnedEvent(
                child_id=record.child_id,
                book_id=record.book_id,
                book_copy_id=copy_id,
                borrow_record_id=record.id,
                reason=condition,
            ),
            db=self.db,
        )
        self.db.commit()
        return record

    # ---------- 续借 ----------
    def renew(self, admin, record_id: int) -> BorrowRecord:
        record = (
            self.db.query(BorrowRecord)
            .filter(BorrowRecord.id == record_id, BorrowRecord.is_deleted == 0)
            .first()
        )
        if not record:
            raise NotFoundError("借阅记录不存在")
        child = self.db.query(Child).filter(Child.id == record.child_id).first()
        if child and child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，续借已冻结")
        # D1：过期/未入会/退会均不能续借（D3/R-313 自助续借行；过期无可放行口径）
        if child and not child.is_active_member:
            state = "已过期" if child.is_expired_member else child.member_status
            raise ValidationError(f"孩子会员状态无效（{state}），不能续借（R-313）")
        if record.status not in (BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE):
            raise ValidationError("该记录不可续借")
        if record.due_at < datetime.now():
            raise ValidationError("已逾期的书不能续借（V1.1 §5.4）")
        if record.renew_used >= 1:
            raise ValidationError("续借机会已用完（每本书限 1 次）")
        renew_days = int(ConfigService(self.db).get_value("renew_days"))
        record.due_at = record.due_at + timedelta(days=renew_days)  # 从原到期日起算
        record.renew_used += 1
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="circulation.renew",
            target_type="borrow",
            target_id=str(record.id),
            detail={"new_due": str(record.due_at)},
            reason="续借",
        )
        self.db.commit()
        return record

    def book_due_remind(self) -> int:
        """借阅即将到期提醒（due_remind_days 节点；每节点一次）。"""
        from backend.common.config_service import ConfigService

        remind_days = [
            int(x)
            for x in ConfigService(self.db).get_value("due_remind_days", "5,3,1,0").split(",")
            if x.strip() != ""
        ]
        if not remind_days:
            return 0
        today = datetime.now().date()
        sent = 0
        for days in sorted(set(remind_days)):
            target = today + timedelta(days=days)
            records = (
                self.db.query(BorrowRecord)
                .filter(
                    BorrowRecord.is_deleted == 0,
                    BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                    BorrowRecord.due_at >= target,
                    BorrowRecord.due_at < target + timedelta(days=1),
                )
                .all()
            )
            for rec in records:
                child = self.db.query(Child).filter(Child.id == rec.child_id).first()
                if not child:
                    continue
                book = self.db.query(Book).filter(Book.id == rec.book_id).first()
                title = book.title if book else f"书目#{rec.book_id}"
                parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
                label = "今天" if days == 0 else f"{days} 天后"
                if NotificationService(self.db).send(
                    parent_id=child.parent_id,
                    scene=SCENE_BORROW_DUE_REMIND,
                    title="借阅到期提醒",
                    content=(
                        f"《{title}》将于{label}（{rec.due_at:%Y-%m-%d}）到期，请及时归还或续借。"
                    ),
                    category=Notification.CATEGORY_BORROW,
                    child_id=child.id,
                    ref_type="borrow_record",
                    ref_id=str(rec.id),
                    dedup_key=str(days),
                    openid=parent.wechat_openid if parent else None,
                ):
                    sent += 1
        if sent:
            self.db.commit()
        return sent

    def overdue_mark(self) -> int:
        """逾期标记落库（接管原"访问列表时惰性标记"）+ 通知家长。幂等。"""
        now = datetime.now()
        overdue = (
            self.db.query(BorrowRecord)
            .filter(
                BorrowRecord.is_deleted == 0,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                BorrowRecord.due_at < now,
            )
            .all()
        )
        marked = 0
        from sqlalchemy import update as sa_update

        for rec in overdue:
            # P1-F5：状态守卫条件写（只 ACTIVE→OVERDUE），防与还书并发时覆盖已 returned
            result = self.db.execute(
                sa_update(BorrowRecord)
                .where(
                    BorrowRecord.id == rec.id,
                    BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                )
                .values(status=BorrowRecord.STATUS_OVERDUE)
            )
            if result.rowcount == 0:
                continue  # 已被并发方还书/推进，跳过（不误发逾期通知）
            rec.status = BorrowRecord.STATUS_OVERDUE
            marked += 1
            child = self.db.query(Child).filter(Child.id == rec.child_id).first()
            if not child:
                continue
            book = self.db.query(Book).filter(Book.id == rec.book_id).first()
            title = book.title if book else f"书目#{rec.book_id}"
            parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
            NotificationService(self.db).send(
                parent_id=child.parent_id,
                scene=SCENE_BORROW_OVERDUE,
                title="图书已逾期",
                content=f"《{title}》已逾期未还（到期日 {rec.due_at:%Y-%m-%d}），请尽快归还。",
                category=Notification.CATEGORY_BORROW,
                child_id=child.id,
                ref_type="borrow_record",
                ref_id=str(rec.id),
                openid=parent.wechat_openid if parent else None,
            )
        if overdue:
            self.db.commit()
        return marked

    # ---------- 逾期列表 ----------
    def overdue_list(self) -> list[tuple[BorrowRecord, Child, Parent, Book]]:
        now = datetime.now()
        rows = (
            self.db.query(BorrowRecord, Child, Parent, Book)
            .join(Child, BorrowRecord.child_id == Child.id)
            .join(Parent, Child.parent_id == Parent.id)
            .join(Book, BorrowRecord.book_id == Book.id)
            .filter(
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.due_at < now,
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.due_at)
            .all()
        )
        # 顺手把状态标成 overdue
        for record, *_ in rows:
            record.status = BorrowRecord.STATUS_OVERDUE
        self.db.commit()
        return rows
