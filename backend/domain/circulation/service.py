# backend/domain/circulation/service.py — 借/还/续借/人工放行
# （V1 无逾期费，2026-09-03 用户裁定：原文件头"逾期扣减"不实现，上线后视运营需要另立任务）
"""并发纪律（模式手册 P10/P11）：锁主体行（Child with_for_update）串行化同一孩子的借书；
副本行锁 + 唯一索引双保险防同一副本并发借出。"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.events import BookBorrowedEvent, BookReturnedEvent, event_bus
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.gateways.payment.types import yuan_to_cents
from backend.common.notification_models import Notification
from backend.common.notifications import (
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
from backend.domain.reading.reservation_ops import settle_orphans_after_copy_gone


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
            # 2026-10-12 审查 P2：原先 int(Decimal) 截断——0.50 元的未结清尾巴被当成 0（骗过闸门）；
            # 统一按**分**比较（与支付网关同单位）
            deposit_unpaid_balance=yuan_to_cents(dep.unpaid_balance or Decimal("0")) if dep else 0,
            override_reason=None,
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
            # 2026-10-12 审查 P1：白名单判据（refunded/refunding 不再放行；部分扣除仍有余额 → 放行）
            paid_status=Deposit.STATUS_PAID,
            partially_deducted_status=Deposit.STATUS_PARTIALLY_DEDUCTED,
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
        # 2026-10-12 拍板（P6）：下架 = 禁借（PRD §4.4 既有口径，代码此前只做了隐藏与停音频）
        if book.status != Book.STATUS_ON:
            raise ValidationError(f"《{book.title}》已下架，不能借出")
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
        # P2 #16（2026-10-16，第三轮大审查）：人工放行原因先 strip 再判空——原先只判真值，
        # 全空格串（"   "）能通过"必须填原因"的闸（额度/押金/会员全绕），留痕还是空白。
        override_reason = (override_reason or "").strip() or None
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

        # ---- 取锁顺序（全局不变量，第四轮大审查 P1-6，2026-10-17）----
        # 本函数必须先锁**预约行**、再锁**副本行**：预约善后漏斗（`handle_copy_unavailable`）、
        # 预约取消/超时释放/核销都是 `Reservation → copy`；此前 borrow 是 `copy → Reservation`
        # （P2 #17 的锁定读修复新引入的边）→ 与漏斗构成 AB-BA：A 持 copy2 等预约行、B 持预约行等 alt=copy2。
        # 现在统一为 `Child → Reservation → copy`。书目标识复用上面重复借阅检查里**已经解析出的普通读结果**
        # （`_c.book_id` / `_b.id`），无需额外查库，也不在锁之前引入新的读。
        from backend.domain.reading.models import Reservation as _Res

        _book_id_for_lock = _dup_book_id
        reserved_rows: list = []
        if _book_id_for_lock:
            reserved_rows = (
                self.db.query(_Res)
                .filter(
                    _Res.child_id == child_id,
                    _Res.book_id == _book_id_for_lock,
                    _Res.status == _Res.STATUS_ACTIVE,
                    _Res.is_deleted == 0,
                )
                .order_by(_Res.id)  # 锁序稳定：多行按 id 升序，与漏斗/取消/核销同向
                .with_for_update()
                .populate_existing()  # 锁后当前读（P1-F4 同款）：等锁期间可能被并发置终态
                .all()
            )

        # 定位副本（在预约行之后加锁，锁序见上）
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
            # 2026-10-12 拍板（P6）：按副本借也要判书目状态（软删/下架都禁借）
            if book is None or book.is_deleted or book.status != Book.STATUS_ON:
                raise ValidationError("该书目已下架或已删除，不能借出")
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
        expired_override = False  # 过期放行借阅：生成「续费跟进」待办（PRD §3.7，2026-10-14 G11）
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
            expired_override = True
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
            # 2026-10-12 审查 P2：原先 int(Decimal) 截断——0.50 元的未结清尾巴被当成 0（骗过闸门）；
            # 统一按**分**比较（与支付网关同单位）
            deposit_unpaid_balance=yuan_to_cents(dep.unpaid_balance or Decimal("0")) if dep else 0,
            override_reason=override_reason,
            unpaid_status=Deposit.STATUS_UNPAID,
            fully_deducted_status=Deposit.STATUS_FULLY_DEDUCTED,
            # 2026-10-12 审查 P1：白名单判据（refunded/refunding 不再放行；部分扣除仍有余额 → 放行）
            paid_status=Deposit.STATUS_PAID,
            partially_deducted_status=Deposit.STATUS_PARTIALLY_DEDUCTED,
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
        # PRD §5.2：确认借出时**自动创建这本书的阅读记录**（第五十九批 G8；实现抽在 reading 域，
        # 原子插入 + `uq_reading_progress_child_book` 唯一索引兜底，书后补音频时分母自愈）
        from backend.domain.reading.service import ensure_progress_row

        ensure_progress_row(
            self.db,
            child_id=child_id,
            book_id=copy.book_id,
            total_seconds=book.audio_duration_seconds or 0,
        )
        # 借到手 = 预约目的达成：把该孩子对**这本书**的在用预约一并关掉（转"已借出"）。
        # 为什么要在这里做（用户 2026-09-21 反馈"预约的书成功借阅了就应该释放预约"）：
        # 预约占额度（FEAT-036），若借到书后预约还停在 active，它会**一直占着 1 个名额**，
        # 且预约管理页长期挂着一条永远核销不掉的单；走预约核销的路径已在 checkout 里置过，
        # 这里兜住"没走核销、直接借出/扫码借出"的路径（幂等：只改 ACTIVE）。
        # P2 #17（2026-10-16，第三轮大审查）：锁定读 + 状态守卫（不是快照读 + 无条件覆盖写）——
        # 并发下（家长此刻取消预约 / 定时任务超时释放）不能把已 cancelled/expired 的单覆盖回
        # `checked_out`。P1-6（2026-10-17）：这一步的锁定读**提前到锁副本之前**了（全局锁序
        # `Reservation → copy`），这里直接复用已锁定的 `reserved_rows`，不再二次查询/加锁；
        # `populate_existing` 给的是等锁后的最新已提交值，状态守卫原样保留。
        for _r in reserved_rows:
            if _r.status != _Res.STATUS_ACTIVE:
                continue
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
        # 放行借书后的「会籍跟进」待办（两处口径同源，逻辑见 followups.py）：
        # · 未入会 →「入会跟进」（R-313）；· 已过期 →「续费跟进」（PRD §3.7，G11）
        from backend.domain.circulation.followups import (
            send_join_followup,
            send_renewal_followup,
        )

        if unpaid_override:
            send_join_followup(
                self.db, child=child, book=book, due_hint=due_hint, override_reason=override_reason
            )
        if expired_override:
            send_renewal_followup(self.db, child=child, book=book, override_reason=override_reason)
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
        """condition: normal / maintenance / lost。

        带损归还（maintenance/lost）与「报损·遗失」同口径（2026-10-15 大审查 P2 #14）：
        除改副本状态外还写一条**赔偿待办**给运营（PRD §3.6；实现见 `catalog.damage_ops`）。
        """
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

        prev_status = copy.status  # P2 #15：判断"这次归还是否**新**引入损坏/遗失"
        was_overdue = record.due_at < datetime.now()
        record.status = BorrowRecord.STATUS_RETURNED
        record.returned_at = datetime.now()
        record.returned_condition = condition
        record.returned_by = admin.id  # 归还操作人（2026-09-21 D 批：借还记录要追得到人）
        if condition == "normal":
            copy.status = BookCopy.STATUS_AVAILABLE
        elif condition == "maintenance":
            # P3-3：预约善后不在锁内做（漏斗按预约→副本取锁）——见 settle_orphans_after_copy_gone
            copy.status = BookCopy.STATUS_MAINTENANCE
        else:  # lost
            record.status = BorrowRecord.STATUS_LOST
            copy.status = BookCopy.STATUS_LOST
        self.db.flush()
        # 赔偿待办只在"**这次归还是损坏/遗失的首次登记**"时写：副本本来就已被「报损·遗失」
        # 标成维护/遗失的（报损不动在借记录），还书只是收尾——再写一条就变成同一次损失两条待办。
        if condition in ("maintenance", "lost") and prev_status not in (
            BookCopy.STATUS_MAINTENANCE,
            BookCopy.STATUS_LOST,
        ):
            from backend.domain.catalog.damage_ops import notify_return_damage_compensation

            notify_return_damage_compensation(
                self.db,
                copy,
                condition=condition,
                admin=admin,
                child_id=record.child_id,
                borrow_record_id=record.id,  # 幂等键：一次借阅 = 一个"不会重来的事实"（P1-3）
            )
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
        if condition in ("maintenance", "lost"):
            # 提交后行锁已释放 → 按漏斗锁序善后（正常路径零命中；实现归预约域，本文件受 800 行约束）
            settle_orphans_after_copy_gone(self.db, copy_id=copy_id, admin=admin)
        return record

    # ---------- 续借 ----------
    def _load_renewable(
        self,
        record_id: int,
        *,
        owner_child_id: int | None = None,
        allow_overdue: bool = False,
        overdue_reason: str | None = None,
    ) -> tuple[BorrowRecord, bool, int]:
        """续借公共链：锁定读 + 全量校验 + 改期（**馆员代办与家长自助单一来源**）。

        2026-10-13（第五十三轮 G6）：把校验链从 `renew` 抽出来，给家长自助
        `renew_self` 复用——两套判据各写一遍必然漂移（本仓"同一判定写两遍"的老教训）。
        `owner_child_id` 传入时按归属校验（越权与不存在**同回应**，不暴露他人记录）。

        2026-10-13（第五十五轮 G7）：`allow_overdue` 给**馆员人工办理**开口（PRD §5.4
        "特殊情况馆员可以人工办理逾期书的续借（留痕）"），此时 `overdue_reason` 必填；
        家长自助不传该参数 → 逾期仍硬拦。返回 `(记录, 是否逾期, 逾期天数)`（天数供审计留痕）。
        """
        record = (
            self.db.query(BorrowRecord)
            .filter(BorrowRecord.id == record_id, BorrowRecord.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # E-1/T21：锁定读，并发双续借防双延期双计数
            .first()
        )
        if not record or (owner_child_id is not None and record.child_id != owner_child_id):
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
        now = datetime.now()
        # 逾期判定用**布尔**（`due_at < now` 但不足 1 天时 `.days == 0`，不能拿天数当判据）
        is_overdue = record.due_at < now
        overdue_days = (now - record.due_at).days if is_overdue else 0
        if is_overdue and not allow_overdue:
            raise ValidationError("已逾期的书不能续借（V1.1 §5.4）")
        if is_overdue and not (overdue_reason or "").strip():
            raise ValidationError("逾期书人工续借必须填写原因（留痕）")
        # P2-6（2026-10-09 审查）：原先写死 `>= 1`，配置项 renew_limit 是**死配置**
        # （运营改了没反应）。现在读配置（默认 1，与文案同源）。
        renew_limit = int(ConfigService(self.db).get_value("renew_limit", "1"))
        if record.renew_used >= renew_limit:
            raise ValidationError(f"续借机会已用完（每本书限 {renew_limit} 次）")
        renew_days = int(ConfigService(self.db).get_value("renew_days"))
        # 未逾期：从原到期日起算（既有口径，G6 自助同源）；逾期人工办理：从**办理时刻**起算
        # （原到期日已过，再从它加天数往往续借后还在逾期——宽限没有意义，G7 口径见 PRD §5.4）。
        base = now if is_overdue else record.due_at
        record.due_at = base + timedelta(days=renew_days)
        record.renew_used += 1
        if is_overdue:
            # 第四轮大审查 P1-4（2026-10-17）：人工续借后**必须把记录推回 `ACTIVE`**。
            # `overdue` 这个状态只由「逾期标记」任务落库，而**到期提醒（`book_due_remind`）与逾期标记
            # 都只扫 `status == ACTIVE`** —— 原实现只改 `due_at` 不改状态，于是续借后的书：
            # ① 新到期日的到期提醒**永远不发**；② 再次逾期时**二轮逾期通知也丢失**（状态还是 overdue，
            # 不会被重新标记/通知）；③ 借阅台永久显示红色「逾期」（而同屏"其中逾期"计数按 `due_at`
            # 算却是 0，两个数互相矛盾）。续借后的新到期日 = 办理时刻 + N 天，必然在未来，故回 ACTIVE 是准确的。
            record.status = BorrowRecord.STATUS_ACTIVE
        self.db.flush()
        return record, is_overdue, overdue_days

    def renew(self, admin, record_id: int, reason: str | None = None) -> BorrowRecord:
        """馆员代办续借（留痕：操作人 = 管理员，action `circulation.renew`）。

        2026-10-13（第五十五轮 G7）：逾期书按 PRD §5.4 允许**人工办理**——必须填 `reason`
        （写进审计，明细含逾期天数），`renew_limit` 上限与家长自助同一条校验、谁都不能突破。
        """
        record, is_overdue, overdue_days = self._load_renewable(
            record_id, allow_overdue=True, overdue_reason=reason
        )
        publish_audit(
            self.db,
            admin=admin,
            action="circulation.renew",
            target_type="borrow",
            target_id=str(record.id),
            detail={
                "new_due": str(record.due_at),
                "overdue": is_overdue,
                "overdue_days": overdue_days,
            },
            reason=(reason or "").strip() or "续借",
        )
        self.db.commit()
        return record

    def renew_self(self, child: Child, record_id: int) -> BorrowRecord:
        """家长自助续借（PRD §5.1「家长在小程序对**未逾期**的书一键续借」）。

        与馆员代办**共用** `_load_renewable`（含锁定读与全部校验），差别只有两条：
        · 归属校验（只能续自己孩子的记录，越权按"记录不存在"回应）；
        · **不写管理员审计**——`audit_logs.actor_id` 语义是管理员 id，家长 id 混入会污染
          操作人追溯；与家长侧其他自助动作（预约创建/取消、退款申请）口径一致，
          自助续借的事实由 `borrow_records.due_at / renew_used` 承载。
        逾期书**硬拦**（不传 `allow_overdue`）：人工办理是馆员线下的口子（G7）。
        """
        record, _, _ = self._load_renewable(record_id, owner_child_id=child.id)
        self.db.commit()
        return record

    def renew_meta(self) -> dict:
        """续借元数据（书架在借列表用：前端不猜"还能不能续"）。"""
        return {
            "renew_limit": int(ConfigService(self.db).get_value("renew_limit", "1")),
            "renew_days": int(ConfigService(self.db).get_value("renew_days")),
        }

    def book_due_remind(self) -> int:
        """借阅即将到期提醒（任务体见 `circulation.reminders`——本文件受 god-file ≤800 约束）。"""
        from backend.domain.circulation.reminders import book_due_remind

        return book_due_remind(self.db)

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
                # P1-2（2026-10-18 第七十五批）：原先不传 dedup_key → 落默认 "1"
                # = 「同一家长 × 场景 × 借阅行**终身一条**」。续借（G7 人工办理）后再次逾期时，
                # 条件 UPDATE 会重新标记 OVERDUE，但通知被唯一索引静默吞掉 → 家长不知道又逾期了。
                # 周期类场景的去重键必须**带周期**（本次到期日）。
                dedup_key=f"overdue:{rec.due_at:%Y%m%d}",
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
