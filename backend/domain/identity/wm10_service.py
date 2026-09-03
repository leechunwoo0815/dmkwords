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
from backend.domain.identity.models import (
    Child,
    Order,
    Parent,
    RefundRequest,
    WithdrawalRequest,
)
from backend.domain.identity.wm_notify import (
    notify_refund_executed,
    notify_refund_reviewed,
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
        paid = order.amount
        # X6 可退卡片三形态：proportional（比例退带折算过程）/ full（全额）/ zero（不可退）
        if amount <= 0:
            mode = "zero"
        elif amount < paid:
            mode = "proportional"
        else:
            mode = "full"
        calc: dict = {"mode": mode}
        if mode == "proportional":
            days_used = self._days_used(order)
            days_total = 30 if order.order_type == Order.TYPE_OBSERVATION else 365
            calc.update(
                {
                    "days_used": days_used,
                    "days_total": days_total,
                    "days_remaining": max(0, days_total - days_used),
                }
            )
        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "order_type": order.order_type,
            "paid_amount": str(paid),
            "refundable_amount": str(amount),
            "rule": self._rule_text(order),
            "calc": calc,
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
            .with_for_update()
            .populate_existing()  # P1-F7：锁定读——apply 查重在锁内进行（并发双申请防僵尸单）
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
    def apply(
        self, child: Child, order_id: int, reason: str, skip_lock_check: bool = False
    ) -> RefundRequest:
        """创建统一退款申请（R-308 七态入口）。

        skip_lock_check：馆员批量路径（T16 活动取消）传 True——活动取消是馆员操作，
        孩子转让/退会锁定不应阻断退款台账创建（家长主动申请仍硬拦截）。"""
        if not reason or not reason.strip():
            raise ValidationError("必须填写退款原因")
        order = self._paid_order(child, order_id)
        # R1（X6 返工）：0 元禁提交——0 元申请会联动创建退会单+锁孩子，
        # 审核员误批即 withdrawn（真业务陷阱，非纯 UX）
        if self._refundable_amount(order) <= 0:
            raise ValidationError("该订单当前无可退金额")
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
        if not skip_lock_check:
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
                .with_for_update()
                .populate_existing()  # B-13：锁定读防并发覆盖
                .first()
            )
            if w and w.status == WithdrawalRequest.STATUS_APPLYING:
                w.status = WithdrawalRequest.STATUS_CANCELLED
                child.operation_locked = 0
            elif w and w.status in (
                WithdrawalRequest.STATUS_REFUNDING,
                WithdrawalRequest.STATUS_PENDING_SETTLE,
            ):
                # T5/B-13：结算退款单终态推进（全部撤销）——全撤退会失败、部分成功 completed
                self._settle_withdrawal_on_terminal(req, w, child)
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
            .with_for_update()
            .populate_existing()  # P1-F1：锁定读，并发 review/execute 串行化
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
                    .with_for_update()
                    .populate_existing()  # B-13：锁定读防并发覆盖
                    .first()
                )
                child = self.db.query(Child).filter(Child.id == req.child_id).first()
                if w and w.status == WithdrawalRequest.STATUS_APPLYING:
                    w.status = WithdrawalRequest.STATUS_REJECTED
                    if child:
                        child.operation_locked = 0
                elif w and w.status in (
                    WithdrawalRequest.STATUS_REFUNDING,
                    WithdrawalRequest.STATUS_PENDING_SETTLE,
                ):
                    # T5/B-13：结算退款单终态推进（全部拒绝）——全拒退会失败、部分成功 completed
                    self._settle_withdrawal_on_terminal(req, w, child)
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
            .with_for_update()
            .populate_existing()  # P1-F1：锁定读，双超管并发执行串行化（防双台账/双押金退款单）
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
                        self._refund_activity_enrollment(order, admin)
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
        # P1-F9（方案 A）：聚合推进并入主事务（原双 commit 事务分裂——主流程提交后、
        # 聚合推进前崩溃 → 退会永久卡 refunding + operation_locked 不解除）。
        # 同事务内 flush 后 ORM 对象已更新，_advance_withdrawal 可见本事务终态；
        # 推进失败 → 整体回滚（退款单不落 refunded），不再半提交。
        if success:
            self.db.flush()  # P1-F9：autoflush=False——先刷本事务修改（refunded），聚合推进的查询才可见
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

    def _settle_withdrawal_on_terminal(
        self, req: RefundRequest, w: WithdrawalRequest, child: Child | None
    ) -> None:
        """结算退款单全部 reject/cancel 时推进退会状态（T5/B-13 20260903）。

        业务语义（用户确认）：全拒/全撤 = 退会整体失败（钱没退，会员资格不剥夺）
        → REJECTED + 解锁；部分成功（refunded_cnt > 0）→ _advance_withdrawal
        语义 completed（幂等）。"""
        self.db.flush()  # 调用方已将本单改终态（rejected/cancelled）落库，open_cnt 才不含自身
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
        if open_cnt:
            return
        refunded_cnt = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.withdrawal_id == w.id,
                RefundRequest.status == RefundRequest.STATUS_REFUNDED,
                RefundRequest.is_deleted == 0,
            )
            .scalar()
        )
        if refunded_cnt > 0:
            self._advance_withdrawal(req.id)  # completed（幂等）
        else:
            w.status = WithdrawalRequest.STATUS_REJECTED
            if child:
                child.operation_locked = 0

    def _refund_activity_enrollment(self, order: Order, admin=None) -> None:
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
            # WM13 L2 回写（T16 随动微调）：execute 联动翻终态后，该活动已无
            # refund_pending 报名 → 聚合待办回写（T16 前由 review_refund approve
            # 即翻终态触发；approve≠终态后改由 execute 侧收口）
            from sqlalchemy import func as _func

            from backend.common.admin_notification_models import AdminNotification
            from backend.common.admin_notifications import AdminNotifyService

            remaining = (
                self.db.query(_func.count(ActivityEnrollment.id))
                .filter(
                    ActivityEnrollment.activity_id == e.activity_id,
                    ActivityEnrollment.status == ActivityEnrollment.STATUS_REFUND_PENDING,
                    ActivityEnrollment.is_deleted == 0,
                )
                .scalar()
                or 0
            )
            if (
                remaining == 0
                and self.db.query(AdminNotification)
                .filter(
                    AdminNotification.ref_type == AdminNotification.REF_ACTIVITY,
                    AdminNotification.ref_id == str(e.activity_id),
                    AdminNotification.is_deleted == 0,
                )
                .count()
            ):
                AdminNotifyService(self.db).mark_handled(
                    ref_type=AdminNotification.REF_ACTIVITY,
                    ref_id=str(e.activity_id),
                    admin=None,
                )


# WithdrawalService 拆出至 wm10_withdrawal_service.py（god file 800 行限制）；
# 此处 re-export 保持既有引用路径（router/miniapp_router/测试）不变
from backend.domain.identity.wm10_withdrawal_service import (  # noqa: E402, F401  # isort: skip
    WithdrawalService,
)
