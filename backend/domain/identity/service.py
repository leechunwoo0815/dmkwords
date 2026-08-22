# backend/domain/identity/service.py — 家长/孩子/订单/会员开通
"""事务纪律：Service 统一 commit；金额全程 Decimal；审计走 EventBus（publish_audit）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order, Parent


class ParentService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, admin, req) -> Parent:
        if (
            self.db.query(func.count(Parent.id))
            .filter(Parent.phone == req.phone, Parent.is_deleted == 0)
            .scalar()
        ):
            raise ConflictError(f"手机号 {req.phone} 已存在家长账号")
        parent = Parent(name=req.name, phone=req.phone, remark=req.remark)
        self.db.add(parent)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="parent.create",
            target_type="parent",
            target_id=str(parent.id),
            detail={"name": req.name, "phone": req.phone},
        )
        self.db.commit()
        return parent

    def get(self, parent_id: int) -> Parent:
        parent = (
            self.db.query(Parent).filter(Parent.id == parent_id, Parent.is_deleted == 0).first()
        )
        if not parent:
            raise NotFoundError("家长不存在")
        return parent

    def list_children(self, parent_id: int) -> list[Child]:
        return (
            self.db.query(Child)
            .filter(Child.parent_id == parent_id, Child.is_deleted == 0)
            .order_by(Child.id)
            .all()
        )


class ChildService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, admin, parent_id: int, req) -> Child:
        ParentService(self.db).get(parent_id)
        child = Child(
            parent_id=parent_id,
            name=req.name,
            english_name=req.english_name,
            gender=req.gender,
            birthday=req.birthday,
            grade=req.grade,
            member_status=Child.MEMBER_NONE,
        )
        self.db.add(child)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="child.create",
            target_type="child",
            target_id=str(child.id),
            detail={"name": req.name, "parent_id": parent_id},
        )
        self.db.commit()
        return child

    def _transition(self, child: Child, new_status: str) -> None:
        if not child.can_transition(new_status):
            raise ValidationError(
                f"会员状态不允许从 {child.member_status} 变更为 {new_status}（转移矩阵拦截）"
            )
        child.member_status = new_status

    def list_children(self, page: int, page_size: int, keyword: str | None, status: str | None):
        q = self.db.query(Child).filter(Child.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.join(Parent, Child.parent_id == Parent.id).filter(
                func.or_(
                    Child.name.like(like),
                    Child.english_name.like(like),
                    Parent.phone.like(like),
                    Parent.name.like(like),
                )
            )
        else:
            q = q.join(Parent, Child.parent_id == Parent.id)
        if status:
            q = q.filter(Child.member_status == status)
        total = q.count()
        rows = (
            q.add_columns(Parent.name, Parent.phone)
            .order_by(Child.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return rows, total


class OrderService:
    """订单创建与人工收款确认（WM3 主路径）；金额从 SystemConfig 读取（数值全配置化）。"""

    def __init__(self, db: Session):
        self.db = db

    def _config_decimal(self, key: str) -> Decimal:
        from backend.common.config_service import ConfigService

        return Decimal(ConfigService(self.db).get_value(key))

    def create(self, admin, req) -> Order:
        child = self.db.query(Child).filter(Child.id == req.child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        parent = ParentService(self.db).get(child.parent_id)

        # 金额计算（服务端唯一权威；二孩 9 折按下单时刻判定 V1.1 §3.1）
        if req.order_type == Order.TYPE_FIRST_ACTIVITY:
            # 99 元每账号一次（R-321）：存在未全额退的已付 99 单则拒绝
            exists = (
                self.db.query(func.count(Order.id))
                .filter(
                    Order.parent_id == parent.id,
                    Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                    Order.status == Order.STATUS_PAID,
                    Order.is_deleted == 0,
                )
                .scalar()
            )
            if exists:
                raise ConflictError("该账号已购买过首场亲子活动（每账号仅一次）")
            amount = self._config_decimal("first_activity_fee")
        elif req.order_type == Order.TYPE_OBSERVATION:
            amount = self._config_decimal("observation_fee")
        elif req.order_type == Order.TYPE_FORMAL:
            base = self._config_decimal("formal_fee")
            discount = self._config_decimal("second_child_discount_percent")
            # 下单时该账号下另有有效会员孩子 → 自动 9 折
            siblings_active = (
                self.db.query(func.count(Child.id))
                .filter(
                    Child.parent_id == parent.id,
                    Child.id != child.id,
                    Child.member_status.in_(
                        [
                            Child.MEMBER_OBSERVATION,
                            Child.MEMBER_PENDING_EVALUATION,
                            Child.MEMBER_FORMAL,
                        ]
                    ),
                    Child.is_deleted == 0,
                )
                .scalar()
            )
            amount = (
                (base * discount / Decimal(100)).quantize(Decimal("0.01"))
                if siblings_active
                else base
            )
        else:
            raise ValidationError("订单类型不正确")

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=req.order_type,
            parent_id=parent.id,
            child_id=child.id,
            amount=amount,
            status=Order.STATUS_PENDING_MANUAL,
            remark=req.remark,
        )
        self.db.add(order)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="order.create",
            target_type="order",
            target_id=order.order_no,
            detail={"type": req.order_type, "amount": str(amount), "child": child.name},
        )
        self.db.commit()
        return order

    def confirm_payment(self, admin, order_id: int, req) -> Order:
        """人工收款确认 → paid → 联动会员开通。"""
        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if not order.can_transition(Order.STATUS_PAID):
            raise ValidationError(f"订单状态 {order.status} 不可确认收款")

        order.status = Order.STATUS_PAID
        order.pay_method = req.pay_method
        order.paid_at = datetime.now()
        order.paid_by = admin.id
        order.remark = req.remark or order.remark
        self.db.flush()

        # ---- 会员开通联动（同一事务）----
        if order.order_type == Order.TYPE_OBSERVATION:
            child = self._open_membership(order.child_id, Child.MEMBER_OBSERVATION, 30)
        elif order.order_type == Order.TYPE_FORMAL:
            child = self.db.query(Child).filter(Child.id == order.child_id).first()
            # 提前续费顺延（V1.1 §3.4）：有效会员且未过期 → 原到期日 +365
            base = (
                child.member_expire
                if (
                    child.is_active_member
                    and child.member_expire
                    and child.member_expire >= date.today()
                )
                else date.today()
            )
            self._transition_member(child, Child.MEMBER_FORMAL)
            child.member_start = child.member_start or date.today()
            child.member_expire = base + timedelta(days=365)
        elif order.order_type == Order.TYPE_FIRST_ACTIVITY:
            child = None  # 99 元不开会员（获客单）

        publish_audit(
            self.db,
            admin=admin,
            action="order.confirm_payment",
            target_type="order",
            target_id=order.order_no,
            detail={
                "amount": str(order.amount),
                "method": req.pay_method,
                "member_status": child.member_status if child else "-",
            },
            reason=req.remark or "人工收款确认",
        )
        self.db.commit()
        return order

    def _open_membership(self, child_id: int, status: str, days: int) -> Child:
        child = self.db.query(Child).filter(Child.id == child_id).first()
        self._transition_member(child, status)
        today = date.today()
        child.member_start = today
        child.member_expire = today + timedelta(days=days)
        self.db.flush()
        return child

    def _transition_member(self, child: Child, new_status: str) -> None:
        if not child.can_transition(new_status):
            raise ValidationError(
                f"会员状态不允许从 {child.member_status} 变更为 {new_status}（转移矩阵拦截）"
            )
        child.member_status = new_status

    def list_orders(self, page: int, page_size: int, status: str | None, keyword: str | None):
        q = self.db.query(Order).filter(Order.is_deleted == 0)
        if status:
            q = q.filter(Order.status == status)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(func.or_(Order.order_no.like(like), Order.remark.like(like)))
        total = q.count()
        orders = q.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        # 关联孩子/家长名
        out = []
        for o in orders:
            child = (
                self.db.query(Child).filter(Child.id == o.child_id).first() if o.child_id else None
            )
            parent = self.db.query(Parent).filter(Parent.id == o.parent_id).first()
            out.append((o, child.name if child else None, parent.name if parent else None))
        return out, total

    def cancel(self, admin, order_id: int) -> Order:
        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if not order.can_transition(Order.STATUS_CANCELLED):
            raise ValidationError(f"订单状态 {order.status} 不可取消")
        order.status = Order.STATUS_CANCELLED
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="order.cancel",
            target_type="order",
            target_id=order.order_no,
            detail={"amount": str(order.amount)},
        )
        self.db.commit()
        return order
