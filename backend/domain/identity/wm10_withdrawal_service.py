# backend/domain/identity/wm10_withdrawal_service.py — 退会申请状态机（R-311 六态）
"""从 wm10_service.py 拆出（god file 800 行限制）：退会 apply/cancel/list/review。

依赖关系保持：router/miniapp_router 引用路径不变（wm10_service re-export）。
"""

from __future__ import annotations

from datetime import datetime

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
from backend.domain.identity.wm10_service import _ensure_not_locked
from backend.domain.identity.wm_notify import (
    notify_withdrawal_reviewed,
)


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
        # P1-F7：锁 Child 主体行（并发双 apply 串行化；operation_locked 写同在锁内）
        child = self.db.query(Child).filter(Child.id == child.id).with_for_update().first() or child
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

    def _settle_items(self, child: Child) -> list[dict]:
        """结算明细（X2 共享）：preview 与 review 调同一份代码，防两套公式漂移。
        返回 [{kind, order_id, deposit_id, order_no, amount, rule}]。"""
        from backend.domain.billing.models import Deposit
        from backend.domain.identity.wm10_service import RefundService

        refund_svc = RefundService(self.db)
        items: list[dict] = []
        # 1) 会员费订单（可退金额 > 0 的 paid 单，按剩余天数比例）
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
            items.append(
                {
                    "kind": RefundRequest.KIND_ORDER,
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "amount": amount,
                    "rule": refund_svc._rule_text(order),
                }
            )
        # 2) 押金（可用余额；已扣除部分不退）
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .first()
        )
        if dep and dep.available_amount > 0:
            items.append(
                {
                    "kind": RefundRequest.KIND_DEPOSIT,
                    "deposit_id": dep.id,
                    "amount": dep.available_amount,
                    "rule": "押金退可用余额（已扣除部分不退）",
                }
            )
        return items

    def settle_preview(self, request_id: int) -> dict:
        """X2 预估结算（审核前明批）：仅 normal 来源且 applying 态可查。"""
        from decimal import Decimal

        req = (
            self.db.query(WithdrawalRequest)
            .filter(WithdrawalRequest.id == request_id, WithdrawalRequest.is_deleted == 0)
            .first()
        )
        if not req or req.status != WithdrawalRequest.STATUS_APPLYING:
            raise NotFoundError("退会申请不存在或不在待审核态")
        if req.source != WithdrawalRequest.SOURCE_NORMAL:
            raise ValidationError("联动退会单由退款/转让审核推进，无需预估")
        child = self.db.query(Child).filter(Child.id == req.child_id).first()
        if not child:
            raise NotFoundError("孩子不存在")
        items = self._settle_items(child)
        total = sum((it["amount"] for it in items), Decimal("0"))
        deposit_balance = next(
            (it["amount"] for it in items if it["kind"] == RefundRequest.KIND_DEPOSIT),
            Decimal("0"),
        )
        return {
            "items": [
                {
                    "kind": it["kind"],
                    "order_no": it.get("order_no"),
                    "amount": str(it["amount"].quantize(Decimal("0.01"))),
                    "rule": it["rule"],
                }
                for it in items
            ],
            "deposit_balance": str(deposit_balance.quantize(Decimal("0.01"))),
            "total": str(total.quantize(Decimal("0.01"))),
        }

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
            # 结算：与 settle_preview 共用 _settle_items（X2 同源，防公式漂移）
            from backend.domain.billing.models import Deposit

            settle = self._settle_items(child)
            deposit_refund_id = None
            for it in settle:
                rr = RefundRequest(
                    kind=it["kind"],
                    order_id=it.get("order_id"),
                    deposit_id=it.get("deposit_id"),
                    withdrawal_id=req.id,
                    child_id=child.id,
                    amount=it["amount"],
                    reason=f"退会结算：{it['rule']}",
                )
                self.db.add(rr)
                self.db.flush()
                if it["kind"] == RefundRequest.KIND_ORDER:
                    order = self.db.query(Order).filter(Order.id == it["order_id"]).first()
                    order.refund_status = Order.REFUND_STATUS_PENDING
                else:
                    deposit_refund_id = rr.id
                    dep = self.db.query(Deposit).filter(Deposit.id == it["deposit_id"]).first()
                    dep.status = Deposit.STATUS_REFUNDING
                refund_ids.append(rr.id)
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
