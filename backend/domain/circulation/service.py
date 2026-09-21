# backend/domain/circulation/service.py — 借/还/续借/人工放行
# （V1 无逾期费，2026-09-03 用户裁定：原文件头"逾期扣减"不实现，上线后视运营需要另立任务）
"""并发纪律（模式手册 P10/P11）：锁主体行（Child with_for_update）串行化同一孩子的借书；
副本行锁 + 唯一索引双保险防同一副本并发借出。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
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
from backend.domain.circulation.borrow_gate import (
    HARD,
    OVERRIDABLE,
    deposit_gate,
    first_block,
    member_gate,
)
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import Child, Parent


class CirculationService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 查询 ----------
    def child_id_by_member_code(self, member_code: str) -> int:
        """会员码 → child_id（借阅台扫会员码；未命中抛 404，调用方应先做格式校验）。"""
        row = (
            self.db.query(Child.id)
            .filter(Child.member_code == member_code, Child.is_deleted == 0)
            .first()
        )
        if not row:
            raise NotFoundError("未找到该会员码对应的孩子")
        return row[0]

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
        # 预约占额度：卡面与 borrow() 必须同公式（上限 − 在借总数(含逾期) − 预约中）。
        # 2026-09-21 甲方口径修订：逾期只占它自己那一个在借名额，不再额外扣减。
        # 历史 bug（两个方向）：旧式 max(0, borrow_limit - len(overdue) - len(active)) 里 active 已含逾期
        #   → 每本逾期多扣 1（无预约时卡面比 borrow() 放行的少 1，用户 2026-09-21 报障"扣 2 本"）；
        #   同时又完全没减预约中 → 有预约时反而多报。两类误差在"1 本逾期 + 1 个预约"时正好抵消
        #   （演示孩现场就是这种巧合），所以只能靠同源公式 + 回归测试锁死，不能靠肉眼对数字。
        from backend.domain.reading.models import Reservation

        reservation_count = (
            self.db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == child_id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .scalar()
        )
        # 借书资格（单一来源；顺序与 borrow() 一致：先会员状态、后押金）
        _gate_m = member_gate(
            member_status=child.member_status,
            is_active_member=child.is_active_member,
            allow_unpaid=ConfigService(self.db).get_value("allow_unpaid_offline_borrow") == "true",
            override_reason=None,
            held=len(active),
            withdrawn_status=Child.MEMBER_WITHDRAWN,
            none_status=Child.MEMBER_NONE,
        )
        _gate_d = deposit_gate(
            deposit_status=dep.status if dep else None,
            deposit_unpaid_balance=int(dep.unpaid_balance or 0) if dep else 0,
            override_reason=None,
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
        )
        _hit = first_block(_gate_m, _gate_d)
        _block = (_hit.reason, _hit.kind == HARD) if _hit else None

        # 在借记录附上书名/副本码（2026-09-21 用户反馈：在借表只显示日期看不出是哪本书）
        _book_ids = {r.book_id for r in active}
        _copy_ids = {r.copy_id for r in active}
        _titles = (
            {
                bid: title
                for bid, title in self.db.query(Book.id, Book.title)
                .filter(Book.id.in_(_book_ids))
                .all()
            }
            if _book_ids
            else {}
        )
        _codes = (
            {
                cid: code
                for cid, code in self.db.query(BookCopy.id, BookCopy.copy_code)
                .filter(BookCopy.id.in_(_copy_ids))
                .all()
            }
            if _copy_ids
            else {}
        )
        for _r in active:
            _r.book_title = _titles.get(_r.book_id, "")
            _r.copy_code = _codes.get(_r.copy_id, "")
        return {
            "child": child,
            "parent": parent,
            "active_borrows": len(active),
            "overdue_count": len(overdue),
            "available_quota": max(0, borrow_limit - len(active) - reservation_count),
            "borrow_limit": borrow_limit,
            "deposit_status": dep.status if dep else "unpaid",
            "deposit_available": str(dep.available_amount) if dep else "0",
            # 卡面"能不能借、为什么不能"（2026-09-21 用户反馈：未入会竟然显示可借 30 本）——
            # 与 borrow() 同一判定来源；hard=True 表示放行也没用（退会/开关未开/未入会已借满）
            "borrow_block": _block[0] if _block else None,
            "borrow_block_hard": bool(_block and _block[1]),
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
        child = (
            self.db.query(Child)
            .filter(Child.id == child_id)
            .with_for_update()
            .populate_existing()
            .first()
        )
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
            copy = (
                self.db.query(BookCopy)
                .filter(BookCopy.id == copy_id)
                .with_for_update()
                .populate_existing()
                .first()
            )
            if not copy:
                raise NotFoundError("副本不存在")
            book = self.db.query(Book).filter(Book.id == copy.book_id).first()
        elif isbn:
            book, copy = self.find_copy_by_isbn(isbn)
            if not copy:
                raise ConflictError(f"《{book.title}》当前无在馆副本")
            copy = (
                self.db.query(BookCopy)
                .filter(BookCopy.id == copy.id)
                .with_for_update()
                .populate_existing()
                .first()
            )
        else:
            raise ValidationError("请提供副本ID或ISBN")

        # ---- 校验链 ----
        warnings: list[str] = []
        unpaid_override = False  # 未入会放行借阅：72 小时借期（R-313）
        # 会员状态段判定：**单一来源**（与卡面提示共用 borrow_gate.member_gate，别在两处各写一遍）
        allow_unpaid = ConfigService(self.db).get_value("allow_unpaid_offline_borrow") == "true"
        held_now = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        gate_m = member_gate(
            member_status=child.member_status,
            is_active_member=child.is_active_member,
            allow_unpaid=allow_unpaid,
            override_reason=override_reason,
            held=held_now,
            withdrawn_status=Child.MEMBER_WITHDRAWN,
            none_status=Child.MEMBER_NONE,
        )
        if gate_m.code and not (gate_m.kind == OVERRIDABLE and override_reason):
            raise ValidationError(gate_m.reason)
        if gate_m.code == "unpaid" and not gate_m.reason:
            unpaid_override = True
            warnings.append(f"未入会临时借书（原因：{override_reason}）：72 小时内归还或入会")
        elif gate_m.code == "expired" and not gate_m.reason:
            warnings.append(f"会员已过期，馆员放行借书（原因：{override_reason}）")

        # 押金校验
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        # 押金段判定：同样走单一来源（B-12/T12 口径：未缴/扣光/未结清赔偿款同险，可人工放行）
        gate_d = deposit_gate(
            deposit_status=dep.status if dep else None,
            deposit_unpaid_balance=int(dep.unpaid_balance or 0) if dep else 0,
            override_reason=override_reason,
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
        )
        if gate_d.code and not (gate_d.kind == OVERRIDABLE and override_reason):
            raise ValidationError(gate_d.reason)
        if gate_d.code and not gate_d.reason:
            warnings.append(f"{gate_d.code.removeprefix('deposit_')}，馆员放行")

        # 借阅上限：30 − 在借数 − active 预约数（E-9 单次扣减 + B-11/T13 预约占额度
        # 双向执行：预约 create 已计入，borrow 侧同步计入，防 28 在借+2 预约仍可再借）
        from backend.domain.reading.models import Reservation

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
        reservation_count = (
            self.db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == child_id,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .scalar()
        )
        quota = borrow_limit - active_count - reservation_count
        if quota <= 0 and not override_reason:
            raise ValidationError(
                f"可借上限已满（上限 {borrow_limit}，在借 {active_count} 本，"
                f"预约占 {reservation_count} 本）"
            )
        if quota <= 0:
            warnings.append(f"超上限放行（在借 {active_count}，预约 {reservation_count}）")

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
        due_hint = "72 小时内归还或入会"
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
        # 借到手 = 预约目的达成：把该孩子对**这本书**的在用预约一并关掉（转"已借出"）。
        # 为什么要在这里做（用户 2026-09-21 反馈"预约的书成功借阅了就应该释放预约"）：
        # 预约占额度（FEAT-036），若借到书后预约还停在 active，它会**一直占着 1 个名额**，
        # 且预约管理页长期挂着一条永远核销不掉的单；走预约核销的路径已在 checkout 里置过，
        # 这里兜住"没走核销、直接借出/扫码借出"的路径（幂等：只改 ACTIVE）。
        from backend.domain.reading.models import Reservation as _Res

        for _r in (
            self.db.query(_Res)
            .filter(
                _Res.child_id == child_id,
                _Res.book_id == book.id,
                _Res.status == _Res.STATUS_ACTIVE,
                _Res.is_deleted == 0,
            )
            .all()
        ):
            _r.status = _Res.STATUS_CHECKED_OUT
            # 顺带把这条预约锁着的**另一册**放回在馆：预约关了却还锁着书，是"鬼锁"——
            # 那册谁都借不走、预约管理页也不再显示它（同书另一副本被借走的场景）。
            if _r.copy_id and _r.copy_id != copy.id:
                _locked = (
                    self.db.query(BookCopy)
                    .filter(BookCopy.id == _r.copy_id, BookCopy.is_deleted == 0)
                    .first()
                )
                if _locked and _locked.status == BookCopy.STATUS_RESERVED:
                    _locked.status = BookCopy.STATUS_AVAILABLE
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
        # R-313：未入会临时借书 → 生成「入会跟进任务」（管理端待办，馆员跟进家长入会）。
        # 幂等：同孩子 dedup_key 固定 → 多次放行只留一条；显示态由 todo_service 按
        # 孩子会员状态实时推导（一旦入会即自动审结）。
        if unpaid_override:
            from backend.common.admin_notifications import AdminNotifyService

            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP,
                title="入会跟进",
                content=(
                    f"{child.name} 未入会临时借书《{book.title}》（{due_hint}）"
                    f"，放行原因：{override_reason}。请跟进家长办理入会。"
                ),
                ref_type=AdminNotification.REF_CHILD,
                ref_id=child.id,
                applicant_name=child.name,
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
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not record:
            raise NotFoundError("该副本没有进行中的借阅")
        copy = (
            self.db.query(BookCopy)
            .filter(BookCopy.id == copy_id)
            .with_for_update()
            .populate_existing()
            .first()
        )

        was_overdue = record.due_at < datetime.now()
        record.status = BorrowRecord.STATUS_RETURNED
        record.returned_at = datetime.now()
        record.returned_condition = condition
        record.returned_by = admin.id  # 归还操作人（2026-09-21 D 批：借还记录要追得到人）
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
            .with_for_update()
            .populate_existing()  # E-1/T21：锁定读，并发双续借防双延期双计数
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
        # E-11/T20：逐行条件 UPDATE 守卫（join 查询不宜整查询加锁，对齐 overdue_mark
        # 先例）——并发还书（RETURNED）不被旧快照复活为逾期；rowcount=0 的行
        # 从展示列表剔除（还了的书不应再出现在逾期催还名单）
        from sqlalchemy import update as sa_update

        kept: list[tuple[BorrowRecord, Child, Parent, Book]] = []
        for row in rows:
            record = row[0]
            result = self.db.execute(
                sa_update(BorrowRecord)
                .where(
                    BorrowRecord.id == record.id,
                    BorrowRecord.status.in_(
                        [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
                    ),
                )
                .values(status=BorrowRecord.STATUS_OVERDUE)
            )
            if result.rowcount == 0:
                continue  # 已被并发还书/推进，跳过且不在名单展示
            record.status = BorrowRecord.STATUS_OVERDUE
            kept.append(row)
        self.db.commit()
        return kept
