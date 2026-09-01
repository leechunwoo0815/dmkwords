# backend/domain/identity/service.py — 家长/孩子/订单/会员开通
"""事务纪律：Service 统一 commit；金额全程 Decimal；审计走 EventBus（publish_audit）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from backend.common.events import OrderPaidEvent, event_bus
from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_MEMBER_EXPIRE_REMIND, NotificationService
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

    def search(self, keyword: str | None = None, limit: int = 20) -> list[Parent]:
        """家长搜索（W1 建档选择器：姓名/手机号模糊匹配）。"""
        q = self.db.query(Parent).filter(Parent.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Parent.name.like(like), Parent.phone.like(like)))
        return q.order_by(Parent.id.desc()).limit(limit).all()

    # ---- WM3-B1 家长编辑/删除（订单守卫）----
    @staticmethod
    def has_orders(db: Session, parent_id: int) -> bool:
        """名下任一孩子存在未删订单（任意状态含 cancelled）→ 守卫口径（用户拍板）。"""
        return (
            db.query(func.count(Order.id))
            .filter(Order.parent_id == parent_id, Order.is_deleted == 0)
            .scalar()
        ) > 0

    @staticmethod
    def children_count(db: Session, parent_ids: list[int]) -> dict[int, int]:
        if not parent_ids:
            return {}
        rows = (
            db.query(Child.parent_id, func.count(Child.id))
            .filter(Child.parent_id.in_(parent_ids), Child.is_deleted == 0)
            .group_by(Child.parent_id)
            .all()
        )
        return dict(rows)

    def update(self, admin, parent_id: int, req) -> Parent:
        parent = self.get(parent_id)
        if self.has_orders(self.db, parent_id):
            raise ConflictError("该家长名下已创建订单，禁止修改")
        changed = []
        if req.name is not None:
            parent.name = req.name
            changed.append("name")
        if req.phone is not None and req.phone != parent.phone:
            dup = (
                self.db.query(func.count(Parent.id))
                .filter(Parent.phone == req.phone, Parent.is_deleted == 0, Parent.id != parent_id)
                .scalar()
            )
            if dup:
                raise ValidationError(f"手机号 {req.phone} 已存在家长账号")
            parent.phone = req.phone
            changed.append("phone")
        if req.remark is not None:
            parent.remark = req.remark
            changed.append("remark")
        if not changed:
            raise ValidationError("没有可更新的字段")
        publish_audit(
            self.db,
            admin=admin,
            action="parent.update",
            target_type="parent",
            target_id=str(parent.id),
            detail={"fields": changed, "phone": parent.phone},
            reason="手机号即小程序登录标识，修改后家长需用新号登录",
        )
        self.db.commit()
        return parent

    def delete(self, admin, parent_id: int) -> None:
        if self.has_orders(self.db, parent_id):
            raise ConflictError("该家长名下已创建订单，禁止删除")
        parent = self.get(parent_id)
        parent.is_deleted = 1
        # 名下未删孩子一并软删（孤儿档案必须清；守卫已确保这些孩子无订单）
        self.db.query(Child).filter(Child.parent_id == parent_id, Child.is_deleted == 0).update(
            {"is_deleted": 1}
        )
        publish_audit(
            self.db,
            admin=admin,
            action="parent.delete",
            target_type="parent",
            target_id=str(parent.id),
            detail={"name": parent.name, "phone": parent.phone},
        )
        self.db.commit()

    def list_page(
        self, page: int, page_size: int, keyword: str | None = None
    ) -> tuple[list[tuple[Parent, int, bool]], int]:
        """家长管理 tab 分页（含 children_count / has_orders）。"""
        q = self.db.query(Parent).filter(Parent.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Parent.name.like(like), Parent.phone.like(like)))
        total = q.count()
        q = q.order_by(Parent.id.desc())
        parents = q.offset((page - 1) * page_size).limit(page_size).all()
        counts = self.children_count(self.db, [p.id for p in parents])
        out = [(p, counts.get(p.id, 0), self.has_orders(self.db, p.id)) for p in parents]
        return out, total


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

    def _get_child(self, child_id: int) -> Child:
        child = self.db.query(Child).filter(Child.id == child_id, Child.is_deleted == 0).first()
        if not child:
            raise NotFoundError("孩子不存在")
        return child

    # ---- WM3-B1 孩子编辑/删除（订单守卫）----
    @staticmethod
    def has_orders(db: Session, child_id: int) -> bool:
        """该孩子存在未删订单（任意状态含 cancelled）→ 守卫口径（用户拍板）。"""
        return (
            db.query(func.count(Order.id))
            .filter(Order.child_id == child_id, Order.is_deleted == 0)
            .scalar()
        ) > 0

    def update_profile(
        self,
        admin,
        child_id: int,
        english_name: str | None = None,
        grade: str | None = None,
        ar_level: str | None = None,
        name: str | None = None,
        gender: int | None = None,
        birthday=None,
    ) -> Child:
        """维护孩子资料（C19 + WM3-B1 扩展：姓名/性别/生日全开）；AR 只升不降 + 订单守卫。"""
        child = self._get_child(child_id)
        if self.has_orders(self.db, child_id):
            raise ConflictError("该孩子已创建订单，禁止修改")
        changed = []
        if name is not None:
            child.name = name
            changed.append("name")
        if gender is not None:
            child.gender = gender
            changed.append("gender")
        if birthday is not None:
            child.birthday = birthday
            changed.append("birthday")
        if english_name is not None:
            child.english_name = english_name or None
            changed.append("english_name")
        if grade is not None:
            child.grade = grade
            changed.append("grade")
        if ar_level is not None:
            current = child.ar_level
            try:
                cur_v = float(current) if current is not None else -1.0
            except ValueError:
                cur_v = 0.0
            try:
                new_v = float(ar_level)
            except ValueError:
                raise ValidationError("AR 值必须是数字（如 3.5）") from None
            if new_v < 0:
                raise ValidationError("AR 值不能为负数")
            if current is None or new_v > cur_v:
                child.ar_level = f"{new_v:.1f}"
                changed.append("ar_level")
            else:
                raise ValidationError(f"AR 值只升不降（当前 {current}，提交 {ar_level}）")
        if not changed:
            raise ValidationError("没有可更新的字段")
        publish_audit(
            self.db,
            admin=admin,
            action="child.update_profile",
            target_type="child",
            target_id=str(child.id),
            detail={"fields": changed, "ar_level": child.ar_level, "grade": child.grade},
            reason="维护孩子资料（老师评估）",
        )
        self.db.commit()
        return child

    def delete(self, admin, child_id: int) -> None:
        """软删孩子档案（WM3-B1；订单守卫 409）。"""
        child = self._get_child(child_id)
        if self.has_orders(self.db, child_id):
            raise ConflictError("该孩子已创建订单，禁止删除")
        child.is_deleted = 1
        publish_audit(
            self.db,
            admin=admin,
            action="child.delete",
            target_type="child",
            target_id=str(child.id),
            detail={"name": child.name, "parent_id": child.parent_id},
        )
        self.db.commit()

    def mark_pending_evaluation(self, admin, child_id: int, reason: str) -> Child:
        """观察期 → 待评估（C13/决策 8：馆员手动标记留痕；到期自动转换任务在 WM11）。"""
        child = self._get_child(child_id)
        if child.member_status != Child.MEMBER_OBSERVATION:
            raise ValidationError(f"仅观察期孩子可标记待评估（当前状态 {child.member_status}）")
        self._transition(child, Child.MEMBER_PENDING_EVALUATION)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="child.mark_pending_evaluation",
            target_type="child",
            target_id=str(child.id),
            detail={
                "child": child.name,
                "from": Child.MEMBER_OBSERVATION,
                "to": Child.MEMBER_PENDING_EVALUATION,
            },
            reason=reason or "观察期到期，标记待评估",
        )
        self.db.commit()
        return child

    def evaluate_approve(self, admin, child_id: int, reason: str) -> Order:
        """评估通过转正（C13/R-101-5）：转正必须支付正式年费 → 创建年费订单（二孩折扣
        沿用 OrderService 判定），收款确认后自动转正式会员。审计与订单同一事务提交。"""
        child = self._get_child(child_id)
        if child.member_status != Child.MEMBER_PENDING_EVALUATION:
            raise ValidationError(f"仅待评估孩子可评估通过转正（当前状态 {child.member_status}）")
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，不能办理转正")
        publish_audit(
            self.db,
            admin=admin,
            action="child.evaluate_approve",
            target_type="child",
            target_id=str(child.id),
            detail={"child": child.name, "next": "创建年费订单，收款确认后转正"},
            reason=reason or "评估通过，转正式会员",
        )
        from backend.domain.identity.schemas import OrderCreateRequest

        req = OrderCreateRequest(
            child_id=child_id,
            order_type=Order.TYPE_FORMAL,
            remark=reason or "评估通过转正",
        )
        return OrderService(self.db).create(admin, req)

    def list_children(self, page: int, page_size: int, keyword: str | None, status: str | None):
        q = self.db.query(Child).filter(Child.is_deleted == 0)
        if keyword:
            like = f"%{keyword}%"
            q = q.join(Parent, Child.parent_id == Parent.id).filter(
                or_(
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

    def expire_due_members(self) -> int:
        """D1 第 3 层（WM11 定时任务）：到期会员落库。

        - 正式会员 member_expire 已过 → expired（读时判定是拦截防线，落库是状态口径/榜单历史）
        - 观察期到期未评估 → pending_evaluation（C13 自动转换；馆员待评估名单）
        幂等：落库后状态不在查询范围，重复跑无副作用。不发家长通知（PRD §十无"已到期"通知项）。
        """
        today = date.today()
        due = (
            self.db.query(Child)
            .filter(
                Child.is_deleted == 0,
                Child.member_status.in_([Child.MEMBER_FORMAL, Child.MEMBER_OBSERVATION]),
                Child.member_expire.isnot(None),
                Child.member_expire < today,
            )
            .all()
        )
        changed = 0
        for child in due:
            if child.member_status == Child.MEMBER_FORMAL:
                self._transition(child, Child.MEMBER_EXPIRED)
            elif child.member_status == Child.MEMBER_OBSERVATION:
                self._transition(child, Child.MEMBER_PENDING_EVALUATION)
            changed += 1
        if changed:
            self.db.commit()
        return changed

    def pending_evaluation_weekly(self) -> int:
        """待评估每周名单（PRD §12 提醒行）：超过 N 天未评估的待评估孩子计数（馆员看板跟进）。"""
        from backend.common.config_service import ConfigService

        threshold = int(ConfigService(self.db).get_value("pending_evaluation_remind_days", "7"))
        cutoff = datetime.now() - timedelta(days=threshold)
        return (
            self.db.query(func.count(Child.id))
            .filter(
                Child.is_deleted == 0,
                Child.member_status == Child.MEMBER_PENDING_EVALUATION,
                Child.update_time < cutoff,
            )
            .scalar()
        )

    def member_expire_remind(self) -> int:
        """会员到期提醒（PRD §12：前 30/14/7 天 + 当天；每节点只发一次）。

        幂等：dedup_key=提醒节点值，唯一索引防重复。只提醒 formal 且未过期。
        """
        from backend.common.config_service import ConfigService

        notify_days = [
            int(x)
            for x in ConfigService(self.db)
            .get_value("member_expire_remind_days", "30,14,7,0")
            .split(",")
            if x.strip() != ""
        ]
        if not notify_days:
            return 0
        today = date.today()
        sent = 0
        for days in sorted(set(notify_days)):
            target = today + timedelta(days=days)
            children = (
                self.db.query(Child)
                .filter(
                    Child.is_deleted == 0,
                    Child.member_status == Child.MEMBER_FORMAL,
                    Child.member_expire.isnot(None),
                    Child.member_expire == target,
                )
                .all()
            )
            for child in children:
                parent = self.db.query(Parent).filter(Parent.id == child.parent_id).first()
                label = "今天" if days == 0 else f"{days} 天后"
                if NotificationService(self.db).send(
                    parent_id=child.parent_id,
                    scene=SCENE_MEMBER_EXPIRE_REMIND,
                    title="会员续费提醒",
                    content=(
                        f"孩子 {child.name} 的正式会员将在{label}（{target:%Y-%m-%d}）到期，"
                        f"请及时续费以免影响阅读。"
                    ),
                    category=Notification.CATEGORY_MEMBER,
                    child_id=child.id,
                    ref_type="child",
                    ref_id=str(child.id),
                    dedup_key=str(days),
                    openid=parent.wechat_openid if parent else None,
                ):
                    sent += 1
        if sent:
            self.db.commit()
        return sent


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
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，不能创建新订单")
        parent = ParentService(self.db).get(child.parent_id)

        # 金额计算（服务端唯一权威；二孩 9 折按下单时刻判定 V1.1 §3.1）
        if req.order_type == Order.TYPE_FIRST_ACTIVITY:
            # 99 元每账号一次（R-321）：存在未被全额退款的已付 99 单则拒绝
            # （refund_status 口径：退款中/失败均占资格；仅 refunded 释放）
            exists = (
                self.db.query(func.count(Order.id))
                .filter(
                    Order.parent_id == parent.id,
                    Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                    Order.status == Order.STATUS_PAID,
                    Order.refund_status != Order.REFUND_STATUS_REFUNDED,
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
            # 下单时该账号下另有有效会员孩子 → 自动 9 折（有效=日期感知口径 D1：过期的 formal 不算）
            siblings = (
                self.db.query(Child)
                .filter(
                    Child.parent_id == parent.id,
                    Child.id != child.id,
                    Child.is_deleted == 0,
                )
                .all()
            )
            siblings_active = any(s.is_active_member for s in siblings)
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

    def _create_activity_order(self, child: Child, activity, fee) -> Order:
        """活动报名订单（家长小程序发起；占名额待收款确认；不 commit 由调用方统一提交）。"""
        import types
        import uuid

        order = Order(
            order_no=f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}",
            order_type=Order.TYPE_ACTIVITY,
            parent_id=child.parent_id,
            child_id=child.id,
            amount=fee,
            status=Order.STATUS_PENDING_MANUAL,
            remark=f"活动报名：{activity.title}",
        )
        self.db.add(order)
        self.db.flush()
        actor = types.SimpleNamespace(id=0, display_name=f"家长(小程序) child={child.id}")
        publish_audit(
            self.db,
            admin=actor,
            action="order.create",
            target_type="order",
            target_id=order.order_no,
            detail={
                "type": Order.TYPE_ACTIVITY,
                "amount": str(fee),
                "child": child.name,
                "activity": activity.title,
            },
        )
        return order

    def refund_order(self, admin, order_id: int, remark: str) -> Order:
        """订单退款执行（超管审核通过后调用；99 元资格随 refunded 状态恢复）。"""
        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if order.status != Order.STATUS_PAID:
            raise ValidationError(f"订单状态 {order.status} 不可退款")
        order.status = Order.STATUS_REFUNDED
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="order.refund",
            target_type="order",
            target_id=order.order_no,
            detail={"amount": str(order.amount), "child_id": order.child_id},
            reason=remark or "退款审核通过",
        )
        self.db.commit()
        return order

    def confirm_payment(self, admin, order_id: int, req) -> Order:
        """人工收款确认 → paid → 联动会员开通。"""
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id, Order.is_deleted == 0)
            .with_for_update()  # P1-F2：锁定读，双管理员并发确认串行化（防双押金记账/到期日覆盖/双事件）
            .first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        if not order.can_transition(Order.STATUS_PAID):
            raise ValidationError(f"订单状态 {order.status} 不可确认收款")

        # P1-F6：99 元首单资格锁内复查（R-321 每账号一次）——apply 查重只查已 PAID，
        # 双端并发可造两笔 pending 后先后确认穿透；order 已行锁（P1-F2），此处锁内复查
        if order.order_type == Order.TYPE_FIRST_ACTIVITY:
            paid_exists = (
                self.db.query(func.count(Order.id))
                .filter(
                    Order.parent_id == order.parent_id,
                    Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                    Order.status == Order.STATUS_PAID,
                    Order.id != order.id,
                    Order.is_deleted == 0,
                )
                .scalar()
            )
            if paid_exists:
                raise ConflictError("该账号已购买过首场亲子活动（每账号仅一次）")

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
        else:
            child = None  # 活动费：不动会员状态
            # 押金类订单联动押金账户（billing 域；同进程同事务）
            if order.order_type in (Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT):
                from backend.domain.billing.service import DepositService

                DepositService(self.db).on_deposit_order_paid(admin, order)
            # 活动费订单 → 报名转正（activity 域；同一事务）
            if order.order_type == Order.TYPE_ACTIVITY:
                from backend.domain.activity.service import ActivityService

                ActivityService(self.db).on_activity_order_paid(order)

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
        event_bus.publish(
            OrderPaidEvent(
                order_id=order.id,
                child_id=order.child_id,
                order_type=order.order_type,
                amount=order.amount,
            ),
            db=self.db,
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

    def list_orders(
        self,
        page: int,
        page_size: int,
        status: str | None,
        keyword: str | None,
        order_by: str | None = None,
    ):
        q = self.db.query(Order).filter(Order.is_deleted == 0)
        if status:
            q = q.filter(Order.status == status)
        if keyword:
            like = f"%{keyword}%"
            q = q.filter(or_(Order.order_no.like(like), Order.remark.like(like)))
        # W7 受控后端排序：白名单映射写死，非法值 422 暴露前端 bug（禁静默回退）
        order_map = {
            "amount_asc": Order.amount.asc(),
            "amount_desc": Order.amount.desc(),
            "created_at_asc": Order.create_time.asc(),
            "created_at_desc": Order.create_time.desc(),
        }
        order_clause = order_map.get(order_by) if order_by else None
        if order_by and order_clause is None:
            raise ValidationError(
                f"order_by 取值非法：{order_by}（白名单：amount/created_at × asc/desc）"
            )
        total = q.count()
        q = q.order_by(order_clause if order_clause is not None else Order.id.desc())
        orders = q.offset((page - 1) * page_size).limit(page_size).all()
        # 关联孩子/家长名
        out = []
        for o in orders:
            child = (
                self.db.query(Child).filter(Child.id == o.child_id).first() if o.child_id else None
            )
            parent = self.db.query(Parent).filter(Parent.id == o.parent_id).first()
            out.append((o, child.name if child else None, parent.name if parent else None))
        return out, total

    def counts(self) -> dict:
        """订单各状态计数（W3/UI 待确认待办；WM13 待办聚合复用，键名语义化不可改）。"""
        q = self.db.query(Order).filter(Order.is_deleted == 0)
        total = q.count()
        by_status = dict(
            q.with_entities(Order.status, func.count(Order.id)).group_by(Order.status).all()
        )
        return {
            "total": total,
            "pending_payment": by_status.get(Order.STATUS_PENDING_PAYMENT, 0),
            "pending_manual_confirm": by_status.get(Order.STATUS_PENDING_MANUAL, 0),
            "paid": by_status.get(Order.STATUS_PAID, 0),
            "cancelled": by_status.get(Order.STATUS_CANCELLED, 0),
            "refunded": by_status.get(Order.STATUS_REFUNDED, 0),
        }

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

    def cancel_timeout_orders(self) -> int:
        """僵尸单清理（P4/FEAT-019）：待支付/待人工确认订单超时自动取消。

        - 超时订单 → cancelled（不发起家长通知，非 PRD 通知项）
        - 活动费订单 → 联动活动报名取消，释放名额
        幂等：已取消状态不在查询范围。
        """
        from backend.common.config_service import ConfigService

        hours = int(ConfigService(self.db).get_value("pending_payment_timeout_hours", "48"))
        cutoff = datetime.now() - timedelta(hours=hours)
        orders = (
            self.db.query(Order)
            .filter(
                Order.is_deleted == 0,
                Order.status.in_([Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL]),
                Order.create_time < cutoff,
            )
            .all()
        )
        if not orders:
            return 0
        for order in orders:
            order.status = Order.STATUS_CANCELLED
            self.db.flush()
            if order.order_type == Order.TYPE_ACTIVITY:
                from backend.domain.activity.service import ActivityService

                ActivityService(self.db).cancel_enrollment_by_order(order)
        self.db.commit()
        return len(orders)

    def first_activity_90d_remind(self) -> int:
        """99 元首场活动购后 90 天提醒转年费（FEAT-068）。每家长一条。"""
        from backend.common.config_service import ConfigService
        from backend.common.notifications import SCENE_MEMBER_EXPIRE_REMIND

        days = int(ConfigService(self.db).get_value("first_activity_90d_remind_days", "90"))
        cutoff = datetime.now() - timedelta(days=days)
        rows = (
            self.db.query(Order)
            .filter(
                Order.is_deleted == 0,
                Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                Order.status == Order.STATUS_PAID,
                Order.paid_at.isnot(None),
                Order.paid_at < cutoff,
            )
            .all()
        )
        sent = 0
        for order in rows:
            parent = self.db.query(Parent).filter(Parent.id == order.parent_id).first()
            if NotificationService(self.db).send(
                parent_id=order.parent_id,
                scene=SCENE_MEMBER_EXPIRE_REMIND,
                title="续费提醒",
                content="您孩子参与的首场 99 元活动已过去 90 天，如需继续阅读成长，可办理正式会员。",
                category=Notification.CATEGORY_MEMBER,
                child_id=order.child_id,
                ref_type="parent",
                ref_id=str(order.parent_id),
                dedup_key="first_activity_90d",
                openid=parent.wechat_openid if parent else None,
            ):
                sent += 1
        if sent:
            self.db.commit()
        return sent
