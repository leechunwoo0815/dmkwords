# backend/domain/identity/transfer_service.py — 权益转让（WM10，从 wm10_service 拆出）
"""R-305 权益转让：16 项前置条件、家长申请/撤销、超管审核 12 步事务（转出方退会 +
押金退款自动发起 + 受让方继承到期日 + WithdrawalRequest 记录 + 年费不退款留痕）。

事务纪律：Service 统一 commit；留痕走审计事件。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_OTHER_TRANSFER_RESULT, NotificationService
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.circulation.models import BorrowRecord
from backend.domain.identity.models import (
    Child,
    RefundRequest,
    TransferRequest,
    WithdrawalRequest,
)


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
        self.db.flush()  # WM13：取自增 id 供通知 ref_id
        source.operation_locked = 1
        target.operation_locked = 1
        # WM13 触发点3：转让申请 → 管理待办通知（同事务，幂等）
        from backend.common.admin_notification_models import AdminNotification
        from backend.common.admin_notifications import AdminNotifyService

        AdminNotifyService(self.db).send(
            scene=AdminNotification.SCENE_TRANSFER_APPLY,
            title="【权益转让】",
            content=(
                f"【权益转让】{parent.name}申请将 {source.name} 的会员权益"
                f"转让给 {target.name}"
            ),
            ref_type=AdminNotification.REF_TRANSFER,
            ref_id=str(req.id),
            applicant_name=f"{parent.name}·{source.name}",
        )
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
            # ---- 同事务 12 步（R-305）----
            req.status = TransferRequest.STATUS_APPROVED
            # 1) 转出方退会（年费不退；历史阅读成果保留）
            source.member_status = Child.MEMBER_WITHDRAWN
            source.withdraw_reason = "membership_transfer"
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
            # 3) 转出方退会记录（WM10-04：生成 WithdrawalRequest，状态随押金退款流程）
            w_req = WithdrawalRequest(
                child_id=source.id,
                source=WithdrawalRequest.SOURCE_TRANSFER,
                reason=f"权益转让 #{req.id} 审核通过，自动退会",
                status=(
                    WithdrawalRequest.STATUS_REFUNDING
                    if deposit_refund_id
                    else WithdrawalRequest.STATUS_COMPLETED
                ),
                review_remark=remark or "转让通过，自动退会",
                reviewed_by=admin.id,
                reviewed_at=datetime.now(),
            )
            self.db.add(w_req)
            self.db.flush()
            if deposit_refund_id:
                dep_rr = (
                    self.db.query(RefundRequest)
                    .filter(RefundRequest.id == deposit_refund_id)
                    .first()
                )
                dep_rr.withdrawal_id = w_req.id
            # 4) 年费不退款独立留痕（R-305 第 3 步）
            publish_audit(
                self.db,
                admin=admin,
                action="transfer.annual_fee_no_refund",
                target_type="child",
                target_id=str(source.id),
                detail={
                    "transfer_id": req.id,
                    "annual_fee_policy": "no_refund",
                    "note": "年费不退款，会员剩余时长已整体转给受让方",
                },
                reason="权益转让：年费不退款留痕（R-305）",
            )
            # 5) 受让方转正式会员，到期日继承
            target.member_status = Child.MEMBER_FORMAL
            target.member_start = date.today()
            target.member_expire = source.member_expire
            # 6) 解锁双方
            source.operation_locked = 0
            target.operation_locked = 0
            # 7) 留痕
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
                    "withdrawal_request_id": w_req.id,
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
        # WM11：权益转让审核结果通知家长（转出/受让同一家长）
        NotificationService(self.db).send(
            parent_id=source.parent_id,
            scene=SCENE_OTHER_TRANSFER_RESULT,
            title="权益转让审核结果",
            content=(
                f"权益转让审核通过：{source.name} 的剩余会期已转给 {target.name}。"
                if approve
                else f"权益转让申请未通过：{remark}"
            ),
            category=Notification.CATEGORY_OTHER,
            ref_type="transfer",
            ref_id=str(req.id),
        )
        self.db.commit()
        return {"id": req.id, "status": req.status}


# Reservation 延迟导入（避免域循环）
from backend.domain.reading.models import Reservation  # noqa: E402
