# backend/domain/billing/service.py — 押金与赔偿（R-312）
"""押金全流程：缴纳（订单确认联动）→ 赔偿扣除 → 补缴 → 退会退款（WM10 接）。金额 Decimal。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.common.config_service import ConfigService
from backend.common.exceptions import NotFoundError, ValidationError
from backend.domain.billing.models import Deposit, DepositLedger
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order


class DepositService:
    def __init__(self, db: Session):
        self.db = db

    def _get_or_create(self, child_id: int) -> Deposit:
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        if not dep:
            dep = Deposit(child_id=child_id, amount=Decimal("0"))
            self.db.add(dep)
            self.db.flush()
        return dep

    def _ledger(
        self, dep: Deposit, entry_type: str, amount: Decimal, reason: str, admin=None, copy_id=None
    ) -> None:
        self.db.add(
            DepositLedger(
                deposit_id=dep.id,
                entry_type=entry_type,
                amount=amount,
                balance_after=dep.available_amount,
                reason=reason,
                related_copy_id=copy_id,
                operator_id=admin.id if admin else None,
            )
        )

    # ---------- 缴纳（订单确认收款联动；identity.service 在 confirm 时调用） ----------
    def on_deposit_order_paid(self, admin, order: Order) -> Deposit:
        """押金订单（deposit / deposit_supplement）支付成功联动。"""
        child = self.db.query(Child).filter(Child.id == order.child_id).first()
        if not child:
            raise NotFoundError("孩子不存在")
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child.id, Deposit.is_deleted == 0)
            .with_for_update()  # P1-F2 双保险：幂等检查基于锁定读（双确认时后到者阻塞后看到已缴）
            .first()
        )
        if dep is None:
            dep = Deposit(child_id=child.id, amount=Decimal("0"))  # 对齐 _get_or_create 建卡分支
            self.db.add(dep)
            self.db.flush()
        standard = Decimal(ConfigService(self.db).get_value("deposit_amount"))

        if order.order_type == Order.TYPE_DEPOSIT:
            if dep.status == Deposit.STATUS_PAID:
                raise ValidationError("押金已缴纳，请勿重复")
            dep.amount = standard
            dep.available_amount = standard
            dep.status = Deposit.STATUS_PAID
            self._ledger(dep, DepositLedger.ENTRY_PAY, order.amount, "押金缴纳", admin)
        else:  # deposit_supplement 补缴（R-312：补至全额）
            if dep.status not in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED):
                raise ValidationError("押金当前无需补缴")
            dep.available_amount = standard
            dep.deducted_amount = (
                dep.deducted_amount - order.amount
                if dep.deducted_amount >= order.amount
                else Decimal("0")
            )
            dep.supplemented_total += order.amount
            dep.unpaid_balance = Decimal("0")
            dep.status = Deposit.STATUS_PAID
            self._ledger(dep, DepositLedger.ENTRY_SUPPLEMENT, order.amount, "押金补缴至全额", admin)

        publish_audit(
            self.db,
            admin=admin,
            action="deposit.pay",
            target_type="deposit",
            target_id=str(dep.id),
            detail={"amount": str(order.amount), "child": child.name},
            reason="押金收款确认",
        )
        from backend.common.events import DepositPaidEvent, event_bus

        event_bus.publish(
            DepositPaidEvent(
                child_id=child.id,
                deposit_id=dep.id,
                amount=order.amount,
            ),
            db=self.db,
        )
        return dep

    def create_deposit_order(self, admin, child_id: int) -> Order:
        """创建押金订单（1200，金额从配置读）。"""
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        dep = self._get_or_create(child.id)
        if dep.status == Deposit.STATUS_PAID:
            raise ValidationError("押金已缴纳")
        amount = Decimal(ConfigService(self.db).get_value("deposit_amount"))
        import uuid
        from datetime import datetime

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=Order.TYPE_DEPOSIT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=amount,
            status=Order.STATUS_PENDING_MANUAL,
        )
        self.db.add(order)
        self.db.flush()
        self.db.commit()
        return order

    def create_supplement_order(self, admin, child_id: int) -> Order:
        """补缴订单：差额 = 标准额 − 可用余额（R-312 公式）。"""
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        dep = self._get_or_create(child.id)
        standard = Decimal(ConfigService(self.db).get_value("deposit_amount"))
        diff = (standard - dep.available_amount).quantize(Decimal("0.01"))
        if diff <= 0:
            raise ValidationError("押金余额充足，无需补缴")
        if dep.status not in (Deposit.STATUS_PARTIALLY_DEDUCTED, Deposit.STATUS_FULLY_DEDUCTED):
            raise ValidationError("押金当前无需补缴")
        import uuid
        from datetime import datetime

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=Order.TYPE_DEPOSIT_SUPPLEMENT,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=diff,
            status=Order.STATUS_PENDING_MANUAL,
        )
        self.db.add(order)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="deposit.supplement_order",
            target_type="deposit",
            target_id=str(dep.id),
            detail={"diff": str(diff)},
            reason="补缴订单创建",
        )
        self.db.commit()
        return order

    # ---------- 赔偿扣除（WM5 还书标记遗失时调用） ----------
    def deduct_for_compensation(
        self, admin, child_id: int, amount: Decimal, reason: str, copy_id: int | None = None
    ) -> Deposit:
        if amount <= 0:  # P0-F4 防御层：负数会反向增加可用余额（多退真钱）
            raise ValidationError("赔偿金额必须大于 0")
        # P1-F3：锁定读——扣款基于当前真实余额计算，双管理员并发扣款串行化
        # （无锁时 B 读旧快照覆盖写 → 账实永久漂移）
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .with_for_update()
            .first()
        )
        if dep is None:
            dep = Deposit(child_id=child_id, amount=Decimal("0"))
            self.db.add(dep)
            self.db.flush()
        if dep.status not in (Deposit.STATUS_PAID, Deposit.STATUS_PARTIALLY_DEDUCTED):
            raise ValidationError("押金未缴纳或状态异常，无法扣除（请先线下协商处理）")
        deduct = min(amount, dep.available_amount)
        over = (amount - deduct).quantize(Decimal("0.01"))
        dep.available_amount = (dep.available_amount - deduct).quantize(Decimal("0.01"))
        dep.deducted_amount = (dep.deducted_amount + deduct).quantize(Decimal("0.01"))
        if over > 0:
            dep.unpaid_balance = (dep.unpaid_balance + over).quantize(Decimal("0.01"))
            dep.status = Deposit.STATUS_FULLY_DEDUCTED
        else:
            dep.status = (
                Deposit.STATUS_FULLY_DEDUCTED
                if dep.available_amount == 0
                else Deposit.STATUS_PARTIALLY_DEDUCTED
            )
        self._ledger(dep, DepositLedger.ENTRY_DEDUCT, amount, reason, admin, copy_id)
        publish_audit(
            self.db,
            admin=admin,
            action="deposit.deduct",
            target_type="deposit",
            target_id=str(dep.id),
            detail={
                "amount": str(amount),
                "deducted": str(deduct),
                "unpaid": str(dep.unpaid_balance),
            },
            reason=reason,
        )
        self.db.flush()
        return dep

    # ---------- 查询 ----------
    def get_by_child(self, child_id: int) -> tuple[Deposit | None, list[DepositLedger]]:
        dep = (
            self.db.query(Deposit)
            .filter(Deposit.child_id == child_id, Deposit.is_deleted == 0)
            .first()
        )
        ledgers = []
        if dep:
            ledgers = (
                self.db.query(DepositLedger)
                .filter(DepositLedger.deposit_id == dep.id, DepositLedger.is_deleted == 0)
                .order_by(DepositLedger.id.desc())
                .all()
            )
        return dep, ledgers

    def list_deposits(self, page: int, page_size: int, status: str | None, keyword: str | None):

        q = (
            self.db.query(Deposit, Child, Order)
            .join(Child, Deposit.child_id == Child.id)
            .outerjoin(
                Order,
                (Order.child_id == Child.id)
                & (Order.order_type == Order.TYPE_DEPOSIT)
                & (Order.status == Order.STATUS_PAID),
            )
            .filter(Deposit.is_deleted == 0)
        )
        if status:
            q = q.filter(Deposit.status == status)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(
                or_(Child.name.like(like), Child.english_name.like(like))
            )  # func.or_ 在 MySQL 生成非法 SQL（E-20260830 族）
        total = q.count()
        rows = q.order_by(Deposit.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return rows, total

    def get_child_deposit_summary(self, child_id: int) -> tuple[Deposit | None, str]:
        dep, _ = self.get_by_child(child_id)
        child = self.db.query(Child).filter(Child.id == child_id).first()
        return dep, (child.name if child else "")

    def deduct(
        self, admin, child_id: int, amount: Decimal, reason: str, copy_id: int | None = None
    ):
        dep = self.deduct_for_compensation(admin, child_id, amount, reason, copy_id)
        child = self.db.query(Child).filter(Child.id == child_id).first()
        self.db.commit()
        return dep, (child.name if child else "")
