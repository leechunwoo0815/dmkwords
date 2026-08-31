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

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification
from backend.common.admin_notifications import AdminNotifyService
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import (
    Child,
    Order,
    Parent,
    RefundRequest,
    TransferRequest,
    WithdrawalRequest,
)
from backend.domain.identity.wm_notify import (
    notify_refund_executed,
    notify_refund_reviewed,
    notify_withdrawal_reviewed,
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
        if not reason or not reason.strip():
            raise ValidationError("必须填写退款原因")
        order = self._paid_order(child, order_id)
        # dup 检查先于锁定检查（同订单重复申请给更具体的错误）
        dup = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.order_id == order_id,
                RefundRequest.status.in_(
                    [
                        RefundRequest.STATUS_PENDING,
                        RefundRequest.STATUS_APPROVED,
                        RefundRequest.STATUS_PROCESSING,
                    ]
                ),
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("该订单已有进行中的退款申请（同一时刻仅一个）")
        _ensure_not_locked(child)

        withdrawal = None
        if order.order_type in (Order.TYPE_OBSERVATION, Order.TYPE_FORMAL):
            # R-309：会员费退款申请本质 = 退会申请（同时创建 + 锁定）
            pending_withdrawal = (
                self.db.query(func.count(WithdrawalRequest.id))
                .filter(
                    WithdrawalRequest.child_id == child.id,
                    WithdrawalRequest.status.in_(
                        [
                            WithdrawalRequest.STATUS_APPLYING,
                            WithdrawalRequest.STATUS_PENDING_SETTLE,
                            WithdrawalRequest.STATUS_REFUNDING,
                        ]
                    ),
                    WithdrawalRequest.is_deleted == 0,
                )
                .scalar()
            )
            if pending_withdrawal:
                raise ConflictError("该孩子已有进行中的退会流程，会员费退款请走退会申请")
            withdrawal = WithdrawalRequest(
                child_id=child.id,
                source=WithdrawalRequest.SOURCE_REFUND,
                reason=f"会员费退款联动退会：{reason.strip()}",
            )
            self.db.add(withdrawal)
            self.db.flush()
            child.operation_locked = 1

        req = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order_id,
            withdrawal_id=withdrawal.id if withdrawal else None,
            child_id=child.id,
            amount=self._refundable_amount(order),
            reason=reason.strip(),
        )
        self.db.add(req)
        self.db.flush()  # WM13：取自增 id 供通知 ref_id
        order.refund_status = Order.REFUND_STATUS_PENDING
        # WM13 触发点1：退款申请 → 管理待办通知（同事务，幂等，content 含原因原文）
        parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
        AdminNotifyService(self.db).send(
            scene=AdminNotification.SCENE_REFUND_APPLY,
            title="【退款申请】",
            content=(
                f"【退款申请】{parent.name if parent else ''}为{child.name}申请退款 "
                f"￥{req.amount}（订单 {order.order_no}）。原因：{req.reason}"
            ),
            ref_type=AdminNotification.REF_REFUND_REQUEST,
            ref_id=str(req.id),
            applicant_name=f"{parent.name if parent else ''}·{child.name}",
            amount=req.amount,
        )
        self.db.commit()
        return req

    def cancel(self, child: Child, request_id: int) -> RefundRequest:
        """家长撤销待审核退款申请（BDD：状态 cancelled、订单恢复）。"""
        req = (
            self.db.query(RefundRequest)
            .filter(
                RefundRequest.id == request_id,
                RefundRequest.child_id == child.id,
                RefundRequest.is_deleted == 0,
            )
            .first()
        )
        if not req:
            raise NotFoundError("退款申请不存在")
        if req.status != RefundRequest.STATUS_PENDING:
            raise ValidationError(f"申请状态 {req.status}，不可撤销")
        req.status = RefundRequest.STATUS_CANCELLED
        if req.order_id:
            order = self.db.query(Order).filter(Order.id == req.order_id).first()
            if order and order.refund_status in (Order.REFUND_STATUS_PENDING,):
                order.refund_status = Order.REFUND_STATUS_NONE
        # 联动退会申请一并撤销 + 解锁（R-309 联动创建的）
        if req.withdrawal_id:
            w = (
                self.db.query(WithdrawalRequest)
                .filter(WithdrawalRequest.id == req.withdrawal_id)
                .first()
            )
            if w and w.status == WithdrawalRequest.STATUS_APPLYING:
                w.status = WithdrawalRequest.STATUS_CANCELLED
                child.operation_locked = 0
        # WM13 L2 回写：家长撤销 → 该单管理待办审计标注来源（幂等）
        AdminNotifyService(self.db).mark_handled(
            ref_type=AdminNotification.REF_REFUND_REQUEST,
            ref_id=str(req.id),
            note="家长已撤销",
        )
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
        """超管审核（R-308）：approve → approved（待执行）；拒绝 → rejected。
        执行（线下打款登记/线上原路）走 execute。"""
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
                if order:
                    order.refund_status = Order.REFUND_STATUS_APPROVED
            # R-309 联动退会：审核通过 → 进入执行阶段（refunding），
            # 失败时 execute 分支才能回 pending_settle 并允许重试
            if req.withdrawal_id:
                w = (
                    self.db.query(WithdrawalRequest)
                    .filter(WithdrawalRequest.id == req.withdrawal_id)
                    .first()
                )
                if w and w.status == WithdrawalRequest.STATUS_APPLYING:
                    w.status = WithdrawalRequest.STATUS_REFUNDING
        else:
            if not remark or not remark.strip():
                raise ValidationError("拒绝退款必须填写原因（家长可见）")
            req.status = RefundRequest.STATUS_REJECTED
            if req.kind == RefundRequest.KIND_ORDER and req.order_id:
                order = self.db.query(Order).filter(Order.id == req.order_id).first()
                if order and order.refund_status == Order.REFUND_STATUS_PENDING:
                    order.refund_status = Order.REFUND_STATUS_NONE
            # 联动退会申请一并拒绝 + 解锁（R-309 联动创建的；拒绝后家长可再申请）
            if req.withdrawal_id:
                w = (
                    self.db.query(WithdrawalRequest)
                    .filter(WithdrawalRequest.id == req.withdrawal_id)
                    .first()
                )
                if w and w.status == WithdrawalRequest.STATUS_APPLYING:
                    w.status = WithdrawalRequest.STATUS_REJECTED
                    child = self.db.query(Child).filter(Child.id == req.child_id).first()
                    if child:
                        child.operation_locked = 0
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
            reason=remark or ("退款审核通过，待执行" if approve else "退款拒绝"),
        )
        # WM11：退款审核结果通知家长
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if child:
            notify_refund_reviewed(self.db, child, req, approve, remark)
        # WM13 L2 回写：审核终态 → 该单管理待办审计回写（幂等）
        AdminNotifyService(self.db).mark_handled(
            ref_type=AdminNotification.REF_REFUND_REQUEST, ref_id=str(req.id), admin=admin
        )
        self.db.commit()
        return {"id": req.id, "status": req.status}

    def execute(self, admin, request_id: int, success: bool, remark: str) -> dict:
        """执行退款（R-308：approved/failed → processing → refunded/failed）。
        线下人工打款登记凭证（remark）即完成；失败可重试。"""
        req = (
            self.db.query(RefundRequest)
            .filter(RefundRequest.id == request_id, RefundRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status not in (
            RefundRequest.STATUS_APPROVED,
            RefundRequest.STATUS_FAILED,
        ):
            raise ValidationError("退款申请不存在或状态不可执行（需先审核通过）")
        req.status = RefundRequest.STATUS_PROCESSING
        self.db.flush()

        if success:
            req.status = RefundRequest.STATUS_REFUNDED
            if req.kind == RefundRequest.KIND_ORDER and req.order_id:
                order = self.db.query(Order).filter(Order.id == req.order_id).first()
                if order and order.status == Order.STATUS_PAID:
                    order.status = Order.STATUS_REFUNDED
                    order.refund_status = Order.REFUND_STATUS_REFUNDED
                    # 活动订单 → 联动报名退款（同事务）
                    if order.order_type == Order.TYPE_ACTIVITY:
                        self._refund_activity_enrollment(order)
                    # 会员费退款成功 → 联动退会（R-310：withdrawn + 自动发起押金退款）
                    if order.order_type in (Order.TYPE_OBSERVATION, Order.TYPE_FORMAL):
                        self._complete_refund_withdrawal(admin, req, order)
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
                            amount=req.amount,
                            balance_after=Decimal("0"),
                            reason=remark or "押金退款执行",
                            operator_id=admin.id,
                        )
                    )
                    dep.available_amount = Decimal("0")
        else:
            if not remark or not remark.strip():
                raise ValidationError("执行失败必须填写原因（留痕）")
            req.status = RefundRequest.STATUS_FAILED
            if req.kind == RefundRequest.KIND_ORDER and req.order_id:
                order = self.db.query(Order).filter(Order.id == req.order_id).first()
                if order:
                    order.refund_status = Order.REFUND_STATUS_FAILED
            # 关联退会流程回待结算（R-311：失败可回 pending_settle 人工重新处理）
            if req.withdrawal_id:
                w = (
                    self.db.query(WithdrawalRequest)
                    .filter(WithdrawalRequest.id == req.withdrawal_id)
                    .first()
                )
                if w and w.status == WithdrawalRequest.STATUS_REFUNDING:
                    w.status = WithdrawalRequest.STATUS_PENDING_SETTLE

        req.review_remark = remark or req.review_remark
        req.reviewed_by = admin.id
        req.reviewed_at = datetime.now()
        publish_audit(
            self.db,
            admin=admin,
            action="refund.execute",
            target_type="refund_request",
            target_id=str(request_id),
            detail={"success": success, "amount": str(req.amount), "kind": req.kind},
            reason=remark or ("退款执行成功" if success else "退款执行失败"),
        )
        # WM11：退款到账 / 退款失败通知家长
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if child:
            notify_refund_executed(self.db, child, req, success, remark)
        # WM13 触发点5（Q5 裁定）：退款执行失败 → 管理待办通知（运营重试入口）
        if not success:
            parent = (
                self.db.query(Parent).filter(Parent.id == child.parent_id).first()
                if child
                else None
            )
            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_REFUND_EXECUTE_FAILED,
                title="【退款执行失败】",
                content=(
                    f"【退款执行失败】{child.name if child else ''}的退款 ￥{req.amount} "
                    f"执行失败：{remark}。请处理后重试"
                ),
                ref_type=AdminNotification.REF_REFUND_REQUEST,
                ref_id=str(req.id),
                applicant_name=f"{parent.name if parent else ''}·{child.name if child else ''}",
                amount=req.amount,
            )
        # WM13 L2 回写（Q5 配套）：执行成功 = refund_execute_failed 预警闭环（幂等）
        if success:
            AdminNotifyService(self.db).mark_handled(
                ref_type=AdminNotification.REF_REFUND_REQUEST, ref_id=str(req.id), admin=admin
            )
        self.db.commit()
        # 聚合推进关联退会流程（全部退款完成 → completed）
        if success:
            self._advance_withdrawal(request_id)
            self.db.commit()
        return {"id": req.id, "status": req.status}

    def _complete_refund_withdrawal(self, admin, req: RefundRequest, order: Order) -> None:
        """会员费退款成功（R-310）：child → withdrawn + 自动发起押金退款。
        退会原因：refund_linked 联动 = user_refund；主动退会结算单 = user_withdrawal。"""
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if not child or child.member_status == Child.MEMBER_WITHDRAWN:
            return
        reason_code = "user_refund"
        if req.withdrawal_id:
            w = (
                self.db.query(WithdrawalRequest)
                .filter(WithdrawalRequest.id == req.withdrawal_id)
                .first()
            )
            if w and w.source == WithdrawalRequest.SOURCE_NORMAL:
                reason_code = "user_withdrawal"
        child.member_status = Child.MEMBER_WITHDRAWN
        child.withdraw_reason = reason_code
        child.operation_locked = 0
        # 自动发起押金退款申请（可用余额；已有进行中押金单则不重复发起）
        from backend.domain.billing.models import Deposit

        existing_dep_rr = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.child_id == child.id,
                RefundRequest.kind == RefundRequest.KIND_DEPOSIT,
                RefundRequest.status.in_(
                    [
                        RefundRequest.STATUS_PENDING,
                        RefundRequest.STATUS_APPROVED,
                        RefundRequest.STATUS_PROCESSING,
                    ]
                ),
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .first()
        )
        if dep and dep.available_amount > 0 and not existing_dep_rr:
            dep_rr = RefundRequest(
                kind=RefundRequest.KIND_DEPOSIT,
                deposit_id=dep.id,
                child_id=child.id,
                amount=dep.available_amount,
                reason="会员费退款成功，自动发起押金退款（R-310）",
            )
            self.db.add(dep_rr)
            self.db.flush()
            dep.status = Deposit.STATUS_REFUNDING

    def _advance_withdrawal(self, refund_request_id: int) -> None:
        """退款执行成功后聚合推进关联退会流程：
        全部关联退款单 refunded → withdrawal completed（退会正式生效已由各路径落）。
        applying 态命中 = refund_linked 联动退会（会员费单成功即完成）。"""
        req = self.db.query(RefundRequest).filter(RefundRequest.id == refund_request_id).first()
        if not req or not req.withdrawal_id:
            return
        w = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == req.withdrawal_id)
            .first()
        )
        if not w or w.status not in (
            WithdrawalRequest.STATUS_REFUNDING,
            WithdrawalRequest.STATUS_APPLYING,
            WithdrawalRequest.STATUS_PENDING_SETTLE,
        ):
            return
        open_cnt = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.withdrawal_id == w.id,
                RefundRequest.status.in_(
                    [
                        RefundRequest.STATUS_PENDING,
                        RefundRequest.STATUS_APPROVED,
                        RefundRequest.STATUS_PROCESSING,
                        RefundRequest.STATUS_FAILED,
                    ]
                ),
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        if open_cnt == 0:
            w.status = WithdrawalRequest.STATUS_COMPLETED
            child = self.db.query(Child).filter(Child.id == w.child_id).first()
            if child:
                if child.member_status != Child.MEMBER_WITHDRAWN:
                    child.member_status = Child.MEMBER_WITHDRAWN
                    child.withdraw_reason = "user_withdrawal"
                child.operation_locked = 0

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
        """退会 7 项前提（R-311/V1.1 §3.5）：返回不满足项（空=可退）。
        遗失/损坏赔偿归入"未结清赔偿"口径（押金 unpaid_balance）。"""
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
        # 进行中转让（WM10-07）
        pending_transfer = (
            self.db.query(func.count(TransferRequest.id))
            .filter(
                TransferRequest.status == TransferRequest.STATUS_PENDING,
                (TransferRequest.source_child_id == child.id)
                | (TransferRequest.target_child_id == child.id),
                TransferRequest.is_deleted == 0,
            )
            .scalar()
        )
        if pending_transfer:
            problems.append("有进行中的权益转让申请")
        # 进行中会员费退款（WM10-07）
        pending_member_refund = (
            self.db.query(func.count(RefundRequest.id))
            .join(Order, RefundRequest.order_id == Order.id)
            .filter(
                RefundRequest.child_id == child.id,
                RefundRequest.status.in_(
                    [
                        RefundRequest.STATUS_PENDING,
                        RefundRequest.STATUS_APPROVED,
                        RefundRequest.STATUS_PROCESSING,
                    ]
                ),
                Order.order_type.in_([Order.TYPE_OBSERVATION, Order.TYPE_FORMAL]),
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        if pending_member_refund:
            problems.append("有进行中的会员费退款申请")
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
                WithdrawalRequest.status.in_(
                    [
                        WithdrawalRequest.STATUS_APPLYING,
                        WithdrawalRequest.STATUS_PENDING_SETTLE,
                        WithdrawalRequest.STATUS_REFUNDING,
                    ]
                ),
                WithdrawalRequest.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("已有进行中的退会申请")
        req = WithdrawalRequest(
            child_id=child.id, source=WithdrawalRequest.SOURCE_NORMAL, reason=reason.strip()
        )
        self.db.add(req)
        self.db.flush()  # WM13：取自增 id 供通知 ref_id
        child.operation_locked = 1  # 冻结：借/约/续/新订单/新测验/退款/转让
        # WM13 触发点2：家长主动退会 → 管理待办通知（同事务，幂等；联动退会不发）
        parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
        AdminNotifyService(self.db).send(
            scene=AdminNotification.SCENE_WITHDRAWAL_APPLY,
            title="【退会申请】",
            content=(
                f"【退会申请】{parent.name if parent else ''}为{child.name}申请退会。"
                f"原因：{req.reason}"
            ),
            ref_type=AdminNotification.REF_WITHDRAWAL_REQUEST,
            ref_id=str(req.id),
            applicant_name=f"{parent.name if parent else ''}·{child.name}",
        )
        self.db.commit()
        return {"id": req.id, "status": req.status, "child_id": child.id}

    def cancel(self, child: Child, request_id: int) -> WithdrawalRequest:
        """家长撤销进行中的退会申请（applying → cancelled + 解锁）。"""
        req = (
            self.db.query(WithdrawalRequest)
            .filter(
                WithdrawalRequest.id == request_id,
                WithdrawalRequest.child_id == child.id,
                WithdrawalRequest.is_deleted == 0,
            )
            .first()
        )
        if not req:
            raise NotFoundError("退会申请不存在")
        if req.status != WithdrawalRequest.STATUS_APPLYING:
            raise ValidationError(f"申请状态 {req.status}，不可撤销")
        req.status = WithdrawalRequest.STATUS_CANCELLED
        child.operation_locked = 0
        # WM13 L2 回写：家长撤销 → 该单管理待办审计标注来源（幂等）
        AdminNotifyService(self.db).mark_handled(
            ref_type=AdminNotification.REF_WITHDRAWAL_REQUEST,
            ref_id=str(req.id),
            note="家长已撤销",
        )
        self.db.commit()
        return req

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
        """R-311 六态流转：approve → pending_settle（结算生成退款单）→ refunding；
        全部退款单 refunded 后由 RefundService.execute 聚合推进 completed。"""
        req = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == request_id, WithdrawalRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status != WithdrawalRequest.STATUS_APPLYING:
            raise ValidationError("退会申请不存在或已处理")
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if not child:
            raise NotFoundError("孩子不存在")
        if approve:
            # 二次校验前提（审核期间可能借了书）
            problems = self._preconditions(child)
            if problems:
                raise ValidationError("审核时前提不满足：" + "；".join(problems))
            req.status = WithdrawalRequest.STATUS_PENDING_SETTLE
            # ---- 结算：计算三类可退金额并生成退款单（R-311：观察期费/年费/押金）----
            refund_ids = []
            # 1) 会员费订单（可退金额 > 0 的 paid 单）
            refund_svc = RefundService(self.db)
            member_orders = (
                self.db.query(Order)
                .filter(
                    Order.child_id == child.id,
                    Order.order_type.in_([Order.TYPE_OBSERVATION, Order.TYPE_FORMAL]),
                    Order.status == Order.STATUS_PAID,
                    Order.is_deleted == 0,
                )
                .all()
            )
            for order in member_orders:
                amount = refund_svc._refundable_amount(order)
                if amount <= 0:
                    continue
                rr = RefundRequest(
                    kind=RefundRequest.KIND_ORDER,
                    order_id=order.id,
                    withdrawal_id=req.id,
                    child_id=child.id,
                    amount=amount,
                    reason="退会结算：会员费按剩余天数比例退",
                )
                self.db.add(rr)
                self.db.flush()
                order.refund_status = Order.REFUND_STATUS_PENDING
                refund_ids.append(rr.id)
            # 2) 押金（可用余额；已扣除部分不退）
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
                    withdrawal_id=req.id,
                    child_id=child.id,
                    amount=dep.available_amount,
                    reason="退会结算：押金退可用余额",
                )
                self.db.add(rr)
                self.db.flush()
                deposit_refund_id = rr.id
                refund_ids.append(rr.id)
                dep.status = Deposit.STATUS_REFUNDING
            # 3) 有退款单 → refunding；无可退 → 直接 completed + withdrawn
            if refund_ids:
                req.status = WithdrawalRequest.STATUS_REFUNDING
            else:
                req.status = WithdrawalRequest.STATUS_COMPLETED
                child.member_status = Child.MEMBER_WITHDRAWN
                child.withdraw_reason = "user_withdrawal"
                child.operation_locked = 0
            publish_audit(
                self.db,
                admin=admin,
                action="withdrawal.approve",
                target_type="child",
                target_id=str(child.id),
                detail={
                    "deposit_refund_id": deposit_refund_id,
                    "deposit_amount": str(dep.available_amount) if dep else "0",
                    "settle_refunds": refund_ids,
                    "withdrawal_status": req.status,
                },
                reason=remark or "退会通过，进入结算",
            )
        else:
            if not remark or not remark.strip():
                raise ValidationError("拒绝退会必须填写原因（家长可见）")
            req.status = WithdrawalRequest.STATUS_REJECTED
            child.operation_locked = 0
        req.review_remark = remark or None
        req.reviewed_by = admin.id
        req.reviewed_at = datetime.now()
        # WM11：退会审核结果通知家长
        notify_withdrawal_reviewed(self.db, child, request_id, approve, remark)
        # WM13 L2 回写：审核终态 → 该单管理待办审计回写（幂等）
        AdminNotifyService(self.db).mark_handled(
            ref_type=AdminNotification.REF_WITHDRAWAL_REQUEST, ref_id=str(req.id), admin=admin
        )
        self.db.commit()
        return {"id": req.id, "status": req.status}


# 权益转让已拆至 transfer_service.py；保留 re-export 兼容既有 import
from backend.domain.identity.transfer_service import TransferService  # noqa: E402, F401  # isort: skip
