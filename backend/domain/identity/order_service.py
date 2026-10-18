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
from backend.common.sql_utils import escape_like
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity import refund_rules
from backend.domain.identity.models import Child, Order, Parent, RefundRequest
from backend.domain.identity.order_guards import OrderGuardMixin


class OrderService(OrderGuardMixin):
    """订单创建与人工收款确认（WM3 主路径）；金额从 SystemConfig 读取（数值全配置化）。"""

    def __init__(self, db: Session):
        self.db = db

    def _config_int(self, key: str, default: int) -> int:
        from backend.common.config_service import ConfigService

        return int(ConfigService(self.db).get_value(key, str(default)))

    def my_orders(self, child_id: int, parent_id: int) -> list[dict]:
        """家长视角订单列表（A-1/T6 下沉）：孩子名下单 ∪ 家长级单（child_id NULL）。"""
        rows = (
            self.db.query(Order)
            .filter(
                or_(
                    Order.child_id == child_id,
                    (Order.child_id.is_(None)) & (Order.parent_id == parent_id),
                ),
                Order.is_deleted == 0,
            )
            .order_by(Order.id.desc())
            .limit(50)
            .all()
        )
        return [
            {
                "id": r.id,
                "order_no": r.order_no,
                "order_type": r.order_type,
                "amount": str(r.amount),
                "status": r.status,
                "refund_status": r.refund_status or "",
                "created_at": str(r.create_time),
                "paid_at": str(r.paid_at) if r.paid_at else None,
            }
            for r in rows
        ]

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
        # 会员类四型的定价抽进 price_member_order：家长端在线下单（WM12-A）与管理端造单同源，
        # 免得两处各写一份二孩折扣/99 元资格判定而悄悄漂移
        if req.order_type in (
            Order.TYPE_FIRST_ACTIVITY,
            Order.TYPE_OBSERVATION,
            Order.TYPE_FORMAL,
            Order.TYPE_DEPOSIT,
        ):
            amount = self.price_member_order(parent, child, req.order_type)
        elif req.order_type == Order.TYPE_ACTIVITY:
            # 管理端活动单收线下到场家长的钱——不联动报名（边界默认值，简报声明）
            if not req.activity_id:
                raise ValidationError("活动订单必须选择活动")
            from backend.domain.activity.models import Activity

            a = (
                self.db.query(Activity)
                .filter(Activity.id == req.activity_id, Activity.is_deleted == 0)
                .first()
            )
            if not a:
                raise NotFoundError("活动不存在")
            if a.status != Activity.STATUS_PUBLISHED:
                raise ValidationError("活动已取消或结束，不能创建活动订单")
            if a.start_at <= datetime.now():
                raise ValidationError("活动已开始，不能创建活动订单")
            # T2（20260907）：名额校验前置——管理端造单即占位，满员不可造单（422）
            # R7：免费活动无收款单语义——防呆硬拦 422（家长端报名即可，无需造单）
            # R8：防重复报名（双倍收费风险）——dup 检查抄家长端 enroll 同款
            from backend.domain.activity.service import ActivityService

            svc = ActivityService(self.db)
            if a.fee is None or a.fee <= 0:
                raise ValidationError("免费活动无需创建订单，请走家长端报名")
            if svc._quota_used(a.id) >= a.max_quota:
                raise ValidationError("活动名额已满，不能创建活动订单")
            from backend.domain.activity.models import ActivityEnrollment

            dup = (
                self.db.query(func.count(ActivityEnrollment.id))
                .filter(
                    ActivityEnrollment.activity_id == req.activity_id,
                    ActivityEnrollment.child_id == child.id,
                    ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                    ActivityEnrollment.is_deleted == 0,
                )
                .scalar()
            )
            if dup:
                raise ValidationError(
                    "该孩子已有此活动的有效报名（待收款/已报名/退款待审），不可重复创建费用订单"
                )
            amount = a.fee
        elif req.order_type == Order.TYPE_CUSTOM:
            # 自定义单：纯资金流水（不参与会员资格/到期日计算——PRD §3.5.2 边界），
            # 可走退款链（_refundable_amount 兜底返全额）
            if not req.remark or not req.remark.strip():
                raise ValidationError("自定义订单必须填写类型说明")
            if req.amount is None or req.amount <= 0:
                raise ValidationError("自定义订单必须填写金额")
            amount = req.amount
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
        if order.order_type == Order.TYPE_ACTIVITY and req.activity_id:
            # T2（用户裁定推翻插修 11 R2"无报名跳过联动"）：管理端活动单=报名代客创建
            # ——造单即占位（PENDING_PAYMENT 占名额+入场券），与家长端报名殊途同归：
            # 确认收款→ENROLLED 转正（on_activity_order_paid 按 order_id 命中）；
            # 取消→联动释放名额；小程序详情 my_enrollment 即见待收款报名
            from backend.domain.activity.models import ActivityEnrollment
            from backend.domain.activity.service import _ticket_code

            e = ActivityEnrollment(
                activity_id=req.activity_id,
                child_id=child.id,
                order_id=order.id,
                ticket_code=_ticket_code(req.activity_id, child.id),
                status=ActivityEnrollment.STATUS_PENDING_PAYMENT,
            )
            self.db.add(e)
            self.db.flush()
            # R7（插修 14）：管理端造单同发【活动报名待确认】（T6 发送点抽方法双链）
            from backend.domain.activity.service import ActivityService

            ActivityService(self.db)._notify_enroll_manual(child, a, e, amount)
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

    def price_member_order(self, parent: Parent, child: Child, order_type: str) -> Decimal:
        """会员类订单定价（**唯一来源**）——实现已抽到 `identity/order_pricing.py`（god-file ≤800，
        2026-10-17 第六十六批）；保留本方法作**委托**，调用方（管理端造单 / 小程序下单 / 购买页 plans）
        零改动。
        """
        from backend.domain.identity.order_pricing import price_member_order

        return price_member_order(self.db, parent, child, order_type)

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

    def refund_order(self, admin, order_id: int, remark: str) -> RefundRequest:
        """超管代家长发起退款申请（B-15 改造 20260903）：原直接翻 REFUNDED 的旁路已废，
        统一走 R-308 审核链——创建 pending 申请，后续 review→execute 与家长申请同链。"""

        order = (
            self.db.query(Order)
            .filter(Order.id == order_id, Order.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # B-15：锁定读防并发旁路
            .first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        if order.status != Order.STATUS_PAID:
            raise ValidationError(f"订单状态 {order.status} 不可退款")
        # P0-B（2026-10-09）：押金是担保账户，不得经订单退款通道出款——钱出了而 Deposit
        # 纹丝不动（余额还能再退一次）、对账跨通道看不见。与家长端 `wm10_service._paid_order` 同口径。
        if order.order_type in (Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT):
            raise ValidationError("押金退款不能单独发起（随退会/权益转让流程自动发起）")
        # P1 #4（2026-10-15 大审查）：transferred + 活动矩阵两道闸（判据与家长侧共用，见 order_guards）
        self.assert_refund_allowed(order)
        # 在途退款单闸（与家长端 apply 同口径）：同订单只允许一笔进行中的申请（L5 事后能报，
        # 但钱已经出了）。
        active = (
            self.db.query(func.count(RefundRequest.id))
            .filter(
                RefundRequest.order_id == order.id,
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
        if active:
            raise ConflictError("该订单已有进行中的退款申请（同一时刻仅一个）")
        # 金额强制复用 _refundable_amount（X6 三形态同源计算，不得另写）
        refundable = refund_rules.refundable_amount(self.db, order)
        if refundable <= 0:
            raise ValidationError("该订单当前无可退金额")
        req = RefundRequest(
            kind=RefundRequest.KIND_ORDER,
            order_id=order.id,
            child_id=order.child_id,
            amount=refundable,
            reason=remark or "超管代发起",
            status=RefundRequest.STATUS_PENDING,
        )
        self.db.add(req)
        self.db.flush()
        order.refund_status = Order.REFUND_STATUS_PENDING
        publish_audit(
            self.db,
            admin=admin,
            action="order.refund_apply",
            target_type="order",
            target_id=order.order_no,
            detail={"amount": str(refundable), "child_id": order.child_id},
            reason=remark or "超管代发起",
        )
        self.db.commit()
        return req

    ALLOWED_VOUCHER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def upload_voucher(self, admin, order_id: int, data: bytes, filename: str) -> Order:
        """收款凭证上传全链（WM3-B2 两步式第一步；Router 只传 bytes）：
        校验仅待人工确认可传（422）→ 统一转 JPG 存储 → 落库 → 失败删文件防孤儿（R-316 口径）。"""
        import os as _os

        from backend.common.file_storage import _uploads_root, read_image_policy, save_voucher_jpg

        order = self.db.query(Order).filter(Order.id == order_id, Order.is_deleted == 0).first()
        if not order:
            raise NotFoundError("订单不存在")
        if order.status != Order.STATUS_PENDING_MANUAL:
            raise ValidationError(f"仅待人工确认订单可上传凭证（当前状态 {order.status}）")
        ext = _os.path.splitext(filename or "")[1]
        if not ext:
            raise ValidationError("凭证文件缺少扩展名")
        rel = save_voucher_jpg(order.order_no, data, ext, read_image_policy(self.db, "doc"))
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
    def new_order_no() -> str:
        """订单号唯一生成口（历史代码内联同款格式；新增调用方一律走这里）。"""
        return f"DMK{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"

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
            .with_for_update()
            .populate_existing()  # P1-F2：锁定读，双管理员并发确认串行化（防双押金记账/到期日覆盖/双事件）
            .first()
        )
        if not order:
            raise NotFoundError("订单不存在")
        if not order.can_transition(Order.STATUS_PAID):
            raise ValidationError(f"订单状态 {order.status} 不可确认收款")
        self._assert_child_collectable(order)  # P9：冻结期禁收款

        if order.order_type == Order.TYPE_FIRST_ACTIVITY:
            self.assert_first_activity_eligible(order)

        self._settle_paid(
            order,
            actor=admin,
            pay_method=req.pay_method,
            remark=req.remark or "",
            payer_id=admin.id,
            action="order.confirm_payment",
            reason=req.remark or "人工收款确认",
        )
        self.db.commit()
        return order

    def _settle_paid(
        self,
        order: Order,
        *,
        actor,
        pay_method: str,
        remark: str,
        payer_id: int | None,
        action: str,
        reason: str,
        transaction_id: str | None = None,
    ) -> None:
        """**收款结算单一链路**：人工确认收款与线上支付回调都调它（WM12-A）。

        为什么必须合并：回调若另写一份"置 paid + 开会员"，会员到期日/押金记账/活动报名转正/
        99 元资格这些口径早晚漂移——那是最贵的一类 bug。调用方负责 commit（本方法只 flush+发事件）。
        """
        # 结算前置三件（99 元资格 / 冻结期两层闸门 / 锁定孩子主体行）必须在"钱动之前且覆盖全部
        # 收款入口"——P0-A 与 P1 #5/#6（2026-10-15 大审查）；理由见 order_guards.prepare_settle。
        locked_child = self.prepare_settle(order)
        order.status = Order.STATUS_PAID
        order.pay_method = pay_method
        # WM3-B2 审查返工 R1：假通道删除——凭证唯一通道为 /voucher 上传端点
        # （落库即校验 JPG 落盘；confirm 收裸 path 无校验属注入面，不做）
        order.paid_at = datetime.now()
        order.paid_by = payer_id
        if remark:
            order.remark = remark
        if transaction_id:
            order.transaction_id = transaction_id
        self.db.flush()

        # ---- 会员开通联动（同一事务）----
        if order.order_type == Order.TYPE_OBSERVATION:
            child = self._open_membership(
                locked_child,
                Child.MEMBER_OBSERVATION,
                self._config_int("observation_period_days", 30),
                actor=actor,
            )
        elif order.order_type == Order.TYPE_FORMAL:
            child = locked_child
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
            self._transition_member(child, Child.MEMBER_FORMAL, actor=actor)
            child.member_start = child.member_start or date.today()
            # 年费时长进配置（审查 P2-3）：改口径只改配置，不发版
            child.member_expire = base + timedelta(days=self._config_int("formal_period_days", 365))
        elif order.order_type == Order.TYPE_FIRST_ACTIVITY:
            child = None  # 99 元不开会员（获客单）
        else:
            child = None  # 活动费：不动会员状态
            # 押金类订单联动押金账户（billing 域；同进程同事务）
            if order.order_type in (Order.TYPE_DEPOSIT, Order.TYPE_DEPOSIT_SUPPLEMENT):
                from backend.domain.billing.service import DepositService

                DepositService(self.db).on_deposit_order_paid(actor, order)
            # 活动费订单 → 按有无报名分流（R2 插修 11）：
            # 家长端报名链（报名→订单→收款→报名转正）联动转正；管理端直建活动单
            # （FEAT-080 §3.5.2 线下收钱语义，从不创建报名）→ 无联动纯资金入账
            if order.order_type == Order.TYPE_ACTIVITY:
                from backend.domain.activity.models import ActivityEnrollment

                has_enrollment = (
                    self.db.query(func.count(ActivityEnrollment.id))
                    .filter(
                        ActivityEnrollment.order_id == order.id,
                        ActivityEnrollment.is_deleted == 0,
                    )
                    .scalar()
                ) > 0
                if has_enrollment:
                    from backend.domain.activity.service import ActivityService

                    e = ActivityService(self.db).on_activity_order_paid(order)
                    # T6：管理待办审计回写（L2 幂等）+ 家长"报名确认+入场券码"通知
                    from backend.common.admin_notifications import AdminNotifyService
                    from backend.common.notification_models import Notification
                    from backend.common.notifications import (
                        SCENE_ACTIVITY_ENROLL,
                        NotificationService,
                    )

                    AdminNotifyService(self.db).mark_handled(
                        ref_type="activity_enrollment",
                        ref_id=e.id,
                        admin=actor,
                        note=action,
                    )
                    if e.status == ActivityEnrollment.STATUS_ENROLLED:
                        from backend.domain.activity.models import Activity

                        act_title = (
                            self.db.query(Activity.title)
                            .filter(Activity.id == e.activity_id)
                            .scalar()
                            or "活动"
                        )
                        NotificationService(self.db).send(
                            parent_id=order.parent_id,
                            scene=SCENE_ACTIVITY_ENROLL,
                            title="报名确认成功",
                            content=f"《{act_title}》收款已确认，报名成功！入场券码 {e.ticket_code}，"
                            "活动当天出示即可。",
                            category=Notification.CATEGORY_ACTIVITY,
                            child_id=order.child_id,
                            ref_type="activity",
                            ref_id=str(e.activity_id),
                            dedup_key=f"paid-{e.id}",
                        )

        publish_audit(
            self.db,
            admin=actor,
            action=action,
            target_type="order",
            target_id=order.order_no,
            detail={
                "amount": str(order.amount),
                "method": pay_method,
                "member_status": child.member_status if child else "-",
                "transaction_id": transaction_id or "",
            },
            reason=reason,
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

    def _open_membership(self, child: Child, status: str, days: int, *, actor=None) -> Child:
        """开通会员关系（调用方须已持该 Child 行锁：并发续费防覆盖，见 prepare_settle）。"""
        if child is None:
            raise NotFoundError("孩子不存在，无法开通会员")
        self._transition_member(child, status, actor=actor)
        today = date.today()
        child.member_start = today
        child.member_expire = today + timedelta(days=days)
        # 押金复位（第四轮大审查 P1-2）现由**会员状态写口**统一触发（P1-1，2026-10-18）：
        # `_transition_member` → `write_member_status` → `DepositService.on_member_status_changed`。
        # 这里不再单独调用——同一件事只留一个触发点，避免"新入口漏接"的老毛病复发。
        self.db.flush()
        return child

    def _transition_member(self, child: Child, new_status: str, *, actor=None) -> None:
        if not child.can_transition(new_status):
            # G13（2026-10-14 第五十九批）：**违规尝试留痕**。这里必须用独立会话写审计——
            # 拒绝要抛异常、主事务（订单结算等）会整体回滚，同事务写的留痕必然丢失。
            from backend.domain.catalog.audit_events import publish_rejection_audit

            publish_rejection_audit(
                actor=actor,
                action="member.transition_rejected",
                target_type="child",
                target_id=str(child.id),
                detail={
                    "child": child.name,
                    "from": child.member_status,
                    "to": new_status,
                    "guard": "ALLOWED_TRANSITIONS（转移矩阵）",
                },
                reason="状态机拦截：转移矩阵不允许该流转",
            )
            raise ValidationError(
                f"会员状态不允许从 {child.member_status} 变更为 {new_status}（转移矩阵拦截）"
            )
        # P1-1（2026-10-18 第七十五批）：走**会员状态唯一写口**——它除了写状态（含待评估时钟），
        # 还负责"重新入会 → 复位已退还押金"这类连带动作。原先只写状态，年费/转让重新入会的
        # 孩子押金永远停在 refunded（四条缴款入口全拒）→ 有效会员却缴不了押金。
        from backend.domain.identity.member_status import write_member_status

        write_member_status(self.db, child, new_status)

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
            like = f"%{escape_like(keyword)}%"
            q = q.filter(
                or_(Order.order_no.like(like, escape="\\"), Order.remark.like(like, escape="\\"))
            )
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
        # 关联孩子/家长名（P2-4，2026-10-11：**批查**——原先每行 2 条 SELECT，20 行 40 条）
        cmap, pmap = _name_maps(self.db, orders)
        out = [(o, cmap.get(o.child_id), pmap.get(o.parent_id)) for o in orders]
        # R10b：活动报名高亮锚点（批量一次查映射，零 N+1）
        order_ids = [o.id for o in orders]
        enrollment_map: dict[int, int] = {}
        if order_ids:
            from backend.domain.activity.models import ActivityEnrollment

            erows = (
                self.db.query(ActivityEnrollment.order_id, ActivityEnrollment.id)
                .filter(
                    ActivityEnrollment.order_id.in_(order_ids),
                    ActivityEnrollment.is_deleted == 0,
                )
                .all()
            )
            enrollment_map = {oid: eid for oid, eid in erows}
        return out, total, enrollment_map

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
        # P1-3（2026-10-09 外部专家复核 + 本地亲验）：**条件 UPDATE 收口**——"钱已收"的单
        # 不能被取消覆盖。实测（A-9 探针，30 轮并发确认收款 × 取消）：原实现"读后写"，
        # **27 轮**出现终态 `cancelled` 却 `paid_at` 置位（钱与押金台账都在，退款链不触发）。
        # 条件 UPDATE 让"先收款"的那一方赢：状态不在待支付/待确认、或已收款 → 影响行数=0 → 拒。
        changed = (
            self.db.query(Order)
            .filter(
                Order.id == order_id,
                Order.is_deleted == 0,
                Order.status.in_([Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL]),
                Order.paid_at.is_(None),
            )
            .update({Order.status: Order.STATUS_CANCELLED}, synchronize_session=False)
        )
        if not changed:
            raise ValidationError("订单已收款或状态已变更，不能取消（请刷新后重试）")
        self.db.expire(order)  # 条件 UPDATE 走的是 SQL：让 ORM 实体重新读库内新状态
        self.db.flush()
        # T2：活动单取消联动报名取消+名额回补（此前仅 timeout 清理链联动，手动 cancel 漏）
        if order.order_type == Order.TYPE_ACTIVITY:
            from backend.domain.activity.service import ActivityService

            ActivityService(self.db).cancel_enrollment_by_order(order)
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
        marked = 0
        from sqlalchemy import update as sa_update

        for order in orders:
            # E-6/T18：条件 UPDATE 守卫（对齐 overdue_mark 先例）——管理员并发
            # confirm_payment 的 PAID 单不被覆盖回 CANCELLED（钱收了单没了）
            # W6 压测（2026-10-18）：`synchronize_session=False`——ORM 版 UPDATE 默认在 session
            # 身份映射里**逐对象求值 WHERE**，本循环的 session 恰装着扫描出的全部待取消单 → O(N²)
            # （6000 条实测 300+ 秒）；下一行手工同步 `order.status`，故语义等价。
            result = self.db.execute(
                sa_update(Order)
                .where(
                    Order.id == order.id,
                    Order.status.in_([Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL]),
                )
                .values(status=Order.STATUS_CANCELLED)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:
                continue  # 状态已被并发事务改变（如收款确认），跳过（含报名联动全部跳过）
            order.status = Order.STATUS_CANCELLED  # ORM 对象同步，供后续联动逻辑
            self.db.flush()
            if order.order_type == Order.TYPE_ACTIVITY:
                from backend.domain.activity.service import ActivityService

                ActivityService(self.db).cancel_enrollment_by_order(order)
            marked += 1
        self.db.commit()
        return marked

    def first_activity_90d_remind(self) -> int:
        """99 元首场活动购后 90 天提醒转年费（FEAT-068）。每家长一条。

        P2-5（2026-10-17 第六十九批·大审查）：原判据只看"99 元单已付满 N 天"，**不看会员态**
        ——已经转正式会员（或已在观察期/待评估）的家庭仍被催"可办理正式会员"；而 dedup 是
        `ref_type=parent` + 固定键（每家长终身一条），发出去就无法纠正。
        现在按**孩子会员态**过滤：只有"还没有会员关系（未入会/已过期/已退会）"才值得催。
        """
        from backend.common.config_service import ConfigService

        days = int(ConfigService(self.db).get_value("first_activity_90d_remind_days", "90"))
        cutoff = datetime.now() - timedelta(days=days)
        rows = (
            self.db.query(Order)
            .join(Child, Child.id == Order.child_id)
            .filter(
                Order.is_deleted == 0,
                Order.order_type == Order.TYPE_FIRST_ACTIVITY,
                Order.status == Order.STATUS_PAID,
                Order.paid_at.isnot(None),
                Order.paid_at < cutoff,
                Child.is_deleted == 0,
                Child.member_status.in_(
                    [Child.MEMBER_NONE, Child.MEMBER_EXPIRED, Child.MEMBER_WITHDRAWN]
                ),
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


def _name_maps(db: Session, orders: list[Order]) -> tuple[dict[int, str], dict[int, str]]:
    """订单行的孩子/家长名**批查**（P2-4，2026-10-11）——每行 2 条 SELECT → 总计 2 条。

    放模块级而非方法内：`order_service.py` 触顶 800 行 god-file 关（同批实测 807 行）。
    """
    cids = {o.child_id for o in orders if o.child_id}
    pids = {o.parent_id for o in orders if o.parent_id}
    cmap = dict(db.query(Child.id, Child.name).filter(Child.id.in_(cids)).all()) if cids else {}
    pmap = dict(db.query(Parent.id, Parent.name).filter(Parent.id.in_(pids)).all()) if pids else {}
    return cmap, pmap
