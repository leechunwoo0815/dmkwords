# backend/domain/identity/wm10_service.py — 退款 / 退会 / 权益转让 / 评估报告
"""红线对齐（V1.1 §3.5 / §11.3 / R-302~R-305）：
- 退款：服务端算可退金额（比例/全额矩阵）；超管逐单审核；拒绝可再申请
- 退会：4 项前提（已还清/无逾期/无未结赔偿）；审核期冻结；通过自动发起押金退款
- 转让：16 条件前置 + 审核二次校验；通过时同事务 6 步（转出退会/押金退款自动发起/
  受让转正式并继承到期日/记录/解锁/留痕）；词数等级积分各是各的（不转）
- 冻结（operation_locked）：禁借/约/续/新订单/新测验/退款/退会/新转让
事务纪律：Service 统一 commit；留痕走审计事件。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import (
    Child,
    Order,
    RefundRequest,
    TransferRequest,
    WithdrawalRequest,
)


def _ensure_not_locked(child: Child) -> None:
    if child.operation_locked:
        raise ValidationError("孩子正在转让/退会审核流程中，相关操作已冻结（审核完成后自动解锁）")


class RefundService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 可退金额（服务端唯一权威） ----------
    def preview(self, child: Child, order_id: int) -> dict:
        order = self._paid_order(child, order_id)
        amount = self._refundable_amount(order)
        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "paid_amount": str(order.amount),
            "refundable_amount": str(amount),
            "rule": self._rule_text(order),
        }

    def _refundable_amount(self, order: Order) -> Decimal:
        if order.order_type == Order.TYPE_OBSERVATION:
            # 按剩余天数比例（30 天观察期）；待评估后退会可退为 0
            days_used = self._days_used(order)
            remaining = max(0, 30 - days_used)
            return (order.amount * Decimal(remaining) / Decimal(30)).quantize(Decimal("0.01"))
        if order.order_type == Order.TYPE_FORMAL:
            # 按剩余天数比例（365 天年费）
            days_used = self._days_used(order)
            remaining = max(0, 365 - days_used)
            return (order.amount * Decimal(remaining) / Decimal(365)).quantize(Decimal("0.01"))
        # 首场活动费 / 活动费：未签到未开始全额（是否可退由审核判断）
        return order.amount

    @staticmethod
    def _days_used(order: Order) -> int:
        if not order.paid_at:
            return 0
        return max(0, (datetime.now() - order.paid_at).days)

    @staticmethod
    def _rule_text(order: Order) -> str:
        if order.order_type == Order.TYPE_OBSERVATION:
            return "观察期费按剩余天数比例退（30 天期，无手续费）"
        if order.order_type == Order.TYPE_FORMAL:
            return "年费按剩余天数比例退（按实付金额）"
        if order.order_type == Order.TYPE_FIRST_ACTIVITY:
            return "未参加全额退（已参加过不退，审核时核对）"
        return "活动费：未签到且未开始全额退"

    def _paid_order(self, child: Child, order_id: int) -> Order:
        order = (
            self.db.query(Order)
            .filter(
                Order.id == order_id,
                Order.child_id == child.id,
                Order.is_deleted == 0,
            )
            .first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        if order.status != Order.STATUS_PAID:
            raise ValidationError(f"订单状态 {order.status}，不可申请退款")
        # 红线（V1.1 §3.5）：押金退款不能单独申请，跟退会/转让流程一起办
        if order.order_type in (Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT):
            raise ValidationError("押金退款不能单独申请（随退会/权益转让流程自动发起）")
        return order

    # ---------- 家长申请 ----------
    def apply(self, child: Child, order_id: int, reason: str) -> RefundRequest:
        _ensure_not_locked(child)
        if not reason or not reason.strip():
            raise ValidationError("必须填写退款原因")
        order = self._paid_order(child, order_id)
        dup = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.order_id == order_id,
                RefundRequest.status == RefundRequest.STATUS_PENDING,
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("该订单已有进行中的退款申请（同一时刻仅一个）")
        req = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order_id,
            child_id=child.id,
            amount=self._refundable_amount(order),
            reason=reason.strip(),
        )
        self.db.add(req)
        self.db.commit()
        return req

    def my_list(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(RefundRequest)
            .filter(RefundRequest.child_id == child.id, RefundRequest.is_deleted == 0)
            .order_by(RefundRequest.id.desc())
            .all()
        )
        return [self._view(r) for r in rows]

    def _view(self, r: RefundRequest) -> dict:
        return {
            "id": r.id,
            "kind": r.kind,
            "order_id": r.order_id,
            "child_id": r.child_id,
            "amount": str(r.amount),
            "reason": r.reason,
            "status": r.status,
            "review_remark": r.review_remark,
            "created_at": str(r.created_at),
        }

    # ---------- 管理端 ----------
    def admin_list(self, status: str | None = None) -> list[dict]:
        q = self.db.query(RefundRequest).filter(RefundRequest.is_deleted == 0)
        if status:
            q = q.filter(RefundRequest.status == status)
        rows = q.order_by(RefundRequest.id.desc()).limit(200).all()
        out = []
        for r in rows:
            v = self._view(r)
            child = self.db.query(Child).filter(Child.id == r.child_id).first()
            v["child_name"] = child.name if child else f"#{r.child_id}"
            if r.order_id:
                order = self.db.query(Order).filter(Order.id == r.order_id).first()
                if order:
                    v["order_no"] = order.order_no
                    v["order_type"] = order.order_type
                    v["pay_method"] = order.pay_method
            out.append(v)
        return out

    def review(self, admin, request_id: int, approve: bool, remark: str) -> dict:
        req = (
            self.db.query(RefundRequest)
            .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status != RefundRequest.STATUS_PENDING:
            raise ValidationError("退款申请不存在或已处理")
        if approve:
            req.status = RefundRequest.STATUS_APPROVED
            if req.kind == RefundRequest.KIND_ORDER and req.order_id:
                order = self.db.query(Order).filter(Order.id == req.order_id).first()
                if order and order.status == Order.STATUS_PAID:
                    order.status = Order.STATUS_REFUNDED
                    # 活动订单 → 联动报名退款（同事务）
                    if order.order_type == Order.TYPE_ACTIVITY:
                        self._refund_activity_enrollment(order)
            elif req.kind == RefundRequest.KIND_DEPOSIT and req.deposit_id:
                from backend.domain.billing.models import Deposit

                dep = self.db.query(Deposit).filter(Deposit.id == req.deposit_id).first()
                if dep:
                    dep.status = Deposit.STATUS_REFUNDED
                    from backend.domain.billing.models import DepositLedger

                    self.db.add(
                        DepositLedger(
                            deposit_id=dep.id,
                            entry_type=DepositLedger.ENTRY_REFUND,
                            amount=dep.available_amount,
                            balance_after=Decimal("0"),
                            reason="退会审核通过，押金退款",
                            operator_id=admin.id,
                        )
                    )
                    dep.available_amount = Decimal("0")
        else:
            if not remark or not remark.strip():
                raise ValidationError("拒绝退款必须填写原因（家长可见）")
            req.status = RefundRequest.STATUS_REJECTED
        req.review_remark = remark or None
        req.reviewed_by = admin.id
        req.reviewed_at = datetime.now()
        publish_audit(
            self.db,
            admin=admin,
            action="refund.review",
            target_type="refund_request",
            target_id=str(request_id),
            detail={"approve": approve, "amount": str(req.amount), "kind": req.kind},
            reason=remark or ("退款通过" if approve else "退款拒绝"),
        )
        self.db.commit()
        return {"id": req.id, "status": req.status}

    def _refund_activity_enrollment(self, order: Order) -> None:
        from backend.domain.activity.models import ActivityEnrollment

        e = (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.order_id == order.id,
                ActivityEnrollment.is_deleted == 0,
            )
            .first()
        )
        if e and e.status in (
            ActivityEnrollment.STATUS_ENROLLED,
            ActivityEnrollment.STATUS_REFUND_PENDING,
        ):
            e.status = ActivityEnrollment.STATUS_REFUNDED


class WithdrawalService:
    def __init__(self, db: Session):
        self.db = db

    def _preconditions(self, child: Child) -> list[str]:
        """退会 4 项前提（V1.1 §3.5）：返回不满足项（空=可退）。"""
        problems = []
        active = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        if active:
            problems.append(f"还有 {active} 本图书未归还")
        now = datetime.now()
        overdue = (
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == child.id,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                BorrowRecord.due_at < now,
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        if overdue:
            problems.append(f"有 {overdue} 本图书逾期未还")
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .first()
        )
        if dep and dep.unpaid_balance and dep.unpaid_balance > 0:
            problems.append(f"有未结清赔偿款 {dep.unpaid_balance} 元")
        return problems

    def apply(self, child: Child, reason: str) -> dict:
        if child.member_status == Child.MEMBER_WITHDRAWN:
            raise ValidationError("孩子已是退会状态")
        if not child.is_active_member:
            raise ValidationError("当前会员状态无需退会")
        _ensure_not_locked(child)
        if not reason or not reason.strip():
            raise ValidationError("必须填写退会原因")
        problems = self._preconditions(child)
        if problems:
            raise ValidationError("退会前提不满足：" + "；".join(problems))
        dup = (
            self.db.query(func.count(WithdrawalRequest.id))
            .filter(
                WithdrawalRequest.child_id == child.id,
                WithdrawalRequest.status == WithdrawalRequest.STATUS_PENDING,
                WithdrawalRequest.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("已有进行中的退会申请")
        req = WithdrawalRequest(child_id=child.id, reason=reason.strip())
        self.db.add(req)
        child.operation_locked = 1  # 冻结：借/约/续/新订单/新测验/退款/转让
        self.db.commit()
        return {"id": req.id, "status": req.status, "child_id": child.id}

    def my_list(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.child_id == child.id, WithdrawalRequest.is_deleted == 0)
            .order_by(WithdrawalRequest.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "child_id": r.child_id,
                "reason": r.reason,
                "status": r.status,
                "review_remark": r.review_remark,
                "created_at": str(r.created_at),
            }
            for r in rows
        ]

    def admin_list(self, status: str | None = None) -> list[dict]:
        q = self.db.query(WithdrawalRequest).filter(WithdrawalRequest.is_deleted == 0)
        if status:
            q = q.filter(WithdrawalRequest.status == status)
        rows = q.order_by(WithdrawalRequest.id.desc()).limit(200).all()
        out = []
        for r in rows:
            child = self.db.query(Child).filter(Child.id == r.child_id).first()
            out.append(
                {
                    "id": r.id,
                    "child_id": r.child_id,
                    "child_name": child.name if child else f"#{r.child_id}",
                    "member_status": child.member_status if child else "",
                    "reason": r.reason,
                    "status": r.status,
                    "review_remark": r.review_remark,
                    "created_at": str(r.created_at),
                }
            )
        return out

    def review(self, admin, request_id: int, approve: bool, remark: str) -> dict:
        req = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == request_id, WithdrawalRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status != WithdrawalRequest.STATUS_PENDING:
            raise ValidationError("退会申请不存在或已处理")
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if not child:
            raise NotFoundError("孩子不存在")
        if approve:
            # 二次校验前提（审核期间可能借了书）
            problems = self._preconditions(child)
            if problems:
                raise ValidationError("审核时前提不满足：" + "；".join(problems))
            req.status = WithdrawalRequest.STATUS_APPROVED
            child.member_status = Child.MEMBER_WITHDRAWN
            child.operation_locked = 0
            # 自动发起押金退款申请（可用余额；已扣除部分不退）
            from backend.domain.billing.models import Deposit

            dep = (
                self.db.query(Deposit)
                .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
                .first()
            )
            deposit_refund_id = None
            if dep and dep.available_amount > 0:
                rr = RefundRequest(
                    kind=RefundRequest.KIND_DEPOSIT,
                    deposit_id=dep.id,
                    child_id=child.id,
                    amount=dep.available_amount,
                    reason="退会审核通过，自动发起押金退款",
                )
                self.db.add(rr)
                self.db.flush()
                deposit_refund_id = rr.id
                dep.status = Deposit.STATUS_REFUNDING
            publish_audit(
                self.db,
                admin=admin,
                action="withdrawal.approve",
                target_type="child",
                target_id=str(child.id),
                detail={
                    "deposit_refund_id": deposit_refund_id,
                    "deposit_amount": str(dep.available_amount) if dep else "0",
                },
                reason=remark or "退会通过",
            )
        else:
            if not remark or not remark.strip():
                raise ValidationError("拒绝退会必须填写原因（家长可见）")
            req.status = WithdrawalRequest.STATUS_REJECTED
            child.operation_locked = 0
        req.review_remark = remark or None
        req.reviewed_by = admin.id
        req.reviewed_at = datetime.now()
        self.db.commit()
        return {"id": req.id, "status": req.status}


class TransferService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 16 项前置条件（返回 [名称, 是否满足] 列表） ----------
    def check_conditions(
        self,
        parent,
        source: Child,
        target: Child,
        exclude_transfer_id: int | None = None,
    ) -> list[dict]:
        """16 项前置条件；exclude_transfer_id 用于审核二次校验（排除本单自身造成的状态）。"""
        skip_lock_checks = exclude_transfer_id is not None
        checks: list[tuple[str, bool]] = []

        def _has_pending(child_id: int) -> bool:
            pending_refund = (
                self.db.query(func.count(RefundRequest.id))
                .filter(
                    RefundRequest.child_id == child_id,
                    RefundRequest.status == RefundRequest.STATUS_PENDING,
                    RefundRequest.is_deleted == 0,
                )
                .scalar()
            )
            pending_withdraw = (
                self.db.query(func.count(WithdrawalRequest.id))
                .filter(
                    WithdrawalRequest.child_id == child_id,
                    WithdrawalRequest.status == WithdrawalRequest.STATUS_PENDING,
                    WithdrawalRequest.is_deleted == 0,
                )
                .scalar()
            )
            return bool(pending_refund or pending_withdraw)

        active_borrows = lambda cid: (  # noqa: E731
            self.db.query(func.count(BorrowRecord.id))
            .filter(
                BorrowRecord.child_id == cid,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .scalar()
        )
        active_reservations = lambda cid: (  # noqa: E731
            self.db.query(func.count(Reservation.id))
            .filter(
                Reservation.child_id == cid,
                Reservation.status == Reservation.STATUS_ACTIVE,
                Reservation.is_deleted == 0,
            )
            .scalar()
        )

        checks.append(
            ("同一家长账号下的两个孩子", source.parent_id == target.parent_id == parent.id)
        )
        checks.append(
            (
                "转出方是正式会员",
                source.member_status == Child.MEMBER_FORMAL and source.is_active_member,
            )
        )
        checks.append(
            (
                "转出方会员剩余时间大于 0",
                bool(source.member_expire and source.member_expire > date.today()),
            )
        )
        if not skip_lock_checks:
            checks.append(("转出方未处于冻结流程", not source.operation_locked))
        checks.append(("转出方没有进行中的申请（退款/退会/转让）", not _has_pending(source.id)))
        checks.append(("转出方图书已全部归还", active_borrows(source.id) == 0))
        checks.append(("转出方没有进行中的预约", active_reservations(source.id) == 0))
        from backend.domain.billing.models import Deposit

        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == source.id, Deposit.is_deleted == 0)
            .first()
        )
        checks.append(
            ("转出方无未结清赔偿款", not (dep and dep.unpaid_balance and dep.unpaid_balance > 0))
        )
        if not skip_lock_checks:
            checks.append(("受让方未处于冻结流程", not target.operation_locked))
        checks.append(
            (
                "受让方从未入会或已退会",
                target.member_status in (Child.MEMBER_NONE, Child.MEMBER_WITHDRAWN),
            )
        )
        checks.append(("受让方没有进行中的申请", not _has_pending(target.id)))
        dup_q = self.db.query(func.count(TransferRequest.id)).filter(
            TransferRequest.status == TransferRequest.STATUS_PENDING,
            TransferRequest.is_deleted == 0,
            (TransferRequest.source_child_id.in_([source.id, target.id]))
            | (TransferRequest.target_child_id.in_([source.id, target.id])),
        )
        if exclude_transfer_id is not None:
            dup_q = dup_q.filter(TransferRequest.id != exclude_transfer_id)
        dup = dup_q.scalar()
        checks.append(("没有进行中的其他转让", not dup))
        return [{"name": n, "ok": ok} for n, ok in checks]

    def apply(self, parent, source_child_id: int, target_child_id: int) -> dict:
        if source_child_id == target_child_id:
            raise ValidationError("转出方和受让方不能是同一个孩子")
        source = self._child(source_child_id)
        target = self._child(target_child_id)
        if source.parent_id != parent.id or target.parent_id != parent.id:
            raise ValidationError("只能在自己账号的孩子之间转让")
        checks = self.check_conditions(parent, source, target)
        failed = [c["name"] for c in checks if not c["ok"]]
        if failed:
            raise ValidationError("转让条件不满足：" + "；".join(failed))
        hours = int(ConfigService(self.db).get_value("transfer_review_timeout_hours"))
        req = TransferRequest(
            source_child_id=source.id,
            target_child_id=target.id,
            expires_at=datetime.now() + timedelta(hours=hours),
        )
        self.db.add(req)
        source.operation_locked = 1
        target.operation_locked = 1
        self.db.commit()
        return {
            "id": req.id,
            "status": req.status,
            "expires_at": str(req.expires_at),
            "conditions": checks,
        }

    def _child(self, child_id: int) -> Child:
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return child

    def my_list(self, parent) -> list[dict]:
        self.expire_overdue()
        child_ids = [
            c.id
            for c in self.db.query(Child.id)
            .filter(Child.parent_id == parent.id, Child.is_deleted == 0)
            .all()
        ]
        if not child_ids:
            return []
        rows = (
            self.db.query(TransferRequest)
            .filter(
                (TransferRequest.source_child_id.in_(child_ids))
                | (TransferRequest.target_child_id.in_(child_ids)),
                TransferRequest.is_deleted == 0,
            )
            .order_by(TransferRequest.id.desc())
            .all()
        )
        return [self._view(r) for r in rows]

    def _view(self, r: TransferRequest) -> dict:
        src = self.db.query(Child).filter(Child.id == r.source_child_id).first()
        tgt = self.db.query(Child).filter(Child.id == r.target_child_id).first()
        return {
            "id": r.id,
            "source_child_id": r.source_child_id,
            "source_name": src.name if src else f"#{r.source_child_id}",
            "target_child_id": r.target_child_id,
            "target_name": tgt.name if tgt else f"#{r.target_child_id}",
            "status": r.status,
            "expires_at": str(r.expires_at),
            "review_remark": r.review_remark,
            "created_at": str(r.created_at),
        }

    def cancel(self, parent, transfer_id: int) -> dict:
        req = self._pending_of(parent, transfer_id)
        req.status = TransferRequest.STATUS_CANCELLED
        self._unlock_both(req)
        self.db.commit()
        return {"id": req.id, "status": req.status}

    def expire_overdue(self) -> int:
        """超时未审 → expired + 双方解锁（列表访问时惰性触发；WM11 定时任务接管）。"""
        rows = (
            self.db.query(TransferRequest)
            .filter(
                TransferRequest.status == TransferRequest.STATUS_PENDING,
                TransferRequest.expires_at < datetime.now(),
                TransferRequest.is_deleted == 0,
            )
            .all()
        )
        for r in rows:
            r.status = TransferRequest.STATUS_EXPIRED
            self._unlock_both(r)
        if rows:
            self.db.commit()
        return len(rows)

    def _unlock_both(self, req: TransferRequest) -> None:
        for cid in (req.source_child_id, req.target_child_id):
            child = self.db.query(Child).filter(Child.id == cid).first()
            if child:
                child.operation_locked = 0

    def _pending_of(self, parent, transfer_id: int) -> TransferRequest:
        req = (
            self.db.query(TransferRequest)
            .filter(TransferRequest.id == transfer_id, TransferRequest.is_deleted == 0)
            .first()
        )
        if not req:
            raise NotFoundError("转让申请不存在")
        child_ids = [
            c.id
            for c in self.db.query(Child.id)
            .filter(Child.parent_id == parent.id, Child.is_deleted == 0)
            .all()
        ]
        if req.source_child_id not in child_ids or req.target_child_id not in child_ids:
            raise ValidationError("无权操作该转让申请")
        if req.status != TransferRequest.STATUS_PENDING:
            raise ValidationError(f"转让状态 {req.status}，不可操作")
        return req

    # ---------- 管理端 ----------
    def admin_list(self, status: str | None = None) -> list[dict]:
        self.expire_overdue()
        q = self.db.query(TransferRequest).filter(TransferRequest.is_deleted == 0)
        if status:
            q = q.filter(TransferRequest.status == status)
        rows = q.order_by(TransferRequest.id.desc()).limit(200).all()
        return [self._view(r) for r in rows]

    def review(self, admin, transfer_id: int, approve: bool, remark: str) -> dict:
        req = (
            self.db.query(TransferRequest)
            .filter(TransferRequest.id == transfer_id, TransferRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status != TransferRequest.STATUS_PENDING:
            raise ValidationError("转让申请不存在或已处理")
        if req.expires_at < datetime.now():
            req.status = TransferRequest.STATUS_EXPIRED
            self._unlock_both(req)
            self.db.commit()
            raise ValidationError("转让已超时自动取消")
        source = self.db.query(Child).filter(Child.id == req.source_child_id).first()
        target = self.db.query(Child).filter(Child.id == req.target_child_id).first()
        if not source or not target:
            raise NotFoundError("孩子不存在")
        if approve:
            # 二次校验（6 项核心：formal/剩余/无借阅/受让资格/无未结/同家长）
            from backend.domain.identity.models import Parent

            parent_obj = self.db.query(Parent).filter(Parent.id == source.parent_id).first()
            checks = self.check_conditions(parent_obj, source, target, exclude_transfer_id=req.id)
            failed = [c["name"] for c in checks if not c["ok"]]
            if failed:
                raise ValidationError("审核时条件不满足：" + "；".join(failed))
            # ---- 同事务 6 步（R-305）----
            req.status = TransferRequest.STATUS_APPROVED
            # 1) 转出方退会（年费不退；历史阅读成果保留）
            source.member_status = Child.MEMBER_WITHDRAWN
            # 2) 自动发起转出方押金退款申请
            from backend.domain.billing.models import Deposit

            dep = (
                self.db.query(Deposit)
                .filter(Deposit.child_id == source.id, Deposit.is_deleted == 0)
                .first()
            )
            deposit_refund_id = None
            if dep and dep.available_amount > 0:
                rr = RefundRequest(
                    kind=RefundRequest.KIND_DEPOSIT,
                    deposit_id=dep.id,
                    child_id=source.id,
                    amount=dep.available_amount,
                    reason="权益转让通过，自动发起押金退款",
                )
                self.db.add(rr)
                self.db.flush()
                deposit_refund_id = rr.id
                dep.status = Deposit.STATUS_REFUNDING
            # 3) 受让方转正式会员，到期日继承
            target.member_status = Child.MEMBER_FORMAL
            target.member_start = date.today()
            target.member_expire = source.member_expire
            # 4) 解锁双方
            source.operation_locked = 0
            target.operation_locked = 0
            # 5) 留痕
            publish_audit(
                self.db,
                admin=admin,
                action="transfer.approve",
                target_type="transfer",
                target_id=str(req.id),
                detail={
                    "source": source.name,
                    "target": target.name,
                    "expire_inherited": str(source.member_expire),
                    "deposit_refund_id": deposit_refund_id,
                },
                reason=remark or "转让通过",
            )
        else:
            if not remark or not remark.strip():
                raise ValidationError("拒绝转让必须填写原因（家长可见）")
            req.status = TransferRequest.STATUS_REJECTED
            self._unlock_both(req)
        req.review_remark = remark or None
        req.reviewed_by = admin.id
        req.reviewed_at = datetime.now()
        self.db.commit()
        return {"id": req.id, "status": req.status}


# Reservation 延迟导入（避免域循环）
from backend.domain.reading.models import Reservation  # noqa: E402
