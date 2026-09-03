# backend/domain/identity/order_service.py — 订单创建/收款确认/退款（god file 拆分自 service.py）
"""WM3 主路径 + WM3-B2 凭证。事务纪律：Service 统一 commit；金额全程 Decimal。"""

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
        # 函数内延迟 import：service.py re-export OrderService（避免拆分循环引用）
        from backend.domain.identity.service import ParentService

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

    ALLOWED_VOUCHER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def upload_voucher(self, admin, order_id: int, data: bytes, filename: str) -> Order:
        """收款凭证上传全链（WM3-B2 两步式第一步；Router 只传 bytes）：
        校验仅待人工确认可传（422）→ 统一转 JPG 存储 → 落库 → 失败删文件防孤儿（R-316 口径）。"""
        import os as _os

        from backend.common.file_storage import _uploads_root, save_voucher_jpg

        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if order.status != Order.STATUS_PENDING_MANUAL:
            raise ValidationError(f"仅待人工确认订单可上传凭证（当前状态 {order.status}）")
        ext = _os.path.splitext(filename or "")[1]
        if not ext:
            raise ValidationError("凭证文件缺少扩展名")
        rel = save_voucher_jpg(order.order_no, data, ext)
        try:
            order.voucher_path = rel
            publish_audit(
                self.db,
                admin=admin,
                action="order.voucher",
                target_type="order",
                target_id=order.order_no,
                detail={"path": rel},
            )
            self.db.commit()
        except Exception:
            # R-316 对齐口径：落库失败删除已写文件，防孤儿文件
            full = _os.path.join(_uploads_root(), rel)
            if _os.path.isfile(full):
                try:
                    _os.remove(full)
                except OSError:
                    pass
            raise
        return order

    @staticmethod
    def get_voucher_rel_path(db: Session, order_id: int) -> str:
        """凭证路径（voucher-image 下发用；NotFound 全在此抛，Router 零 ORM）。"""
        order = db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order or not order.voucher_path:
            raise NotFoundError("凭证不存在")
        return order.voucher_path

    def confirm_payment(self, admin, order_id: int, req) -> Order:
        """人工收款确认 → paid → 联动会员开通。"""
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id, Order.is_deleted == 0)
            .with_for_update().populate_existing()  # P1-F2：锁定读，双管理员并发确认串行化（防双押金记账/到期日覆盖/双事件）
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
        # WM3-B2 审查返工 R1：假通道删除——凭证唯一通道为 /voucher 上传端点
        # （落库即校验 JPG 落盘；confirm 收裸 path 无校验属注入面，不做）
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
