# backend/domain/activity/cancel_flow.py — 活动取消整场（从 activity/service.py 抽出）
"""为什么单开：`activity/service.py` 触及架构关 god-file 上限（>800 行）。

活动取消是自洽的一条链：锁活动行 → 逐报名处置（已付→退款待审、未付→作废关联订单）→
家长通知 → 馆员待办 → 审计。以 mixin 挂回 `ActivityService`，`self.db` 等引用原样成立。
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func

from backend.common.exceptions import ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_ACTIVITY_CANCEL, NotificationService
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order


class ActivityCancelMixin:
    def cancel_activity(self, admin, activity_id: int) -> dict:
        """取消整场：已付未签到 → 退款待审（逐单人工审）；待支付 → 取消。"""
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # E-5/T17：取消全程锁活动行，与 enroll 活动锁串行化
            .first()
        )
        if a.status != Activity.STATUS_PUBLISHED and not (a.status == Activity.STATUS_CANCELLED):
            raise ValidationError("活动状态不可取消")
        # T31 断点续跑幂等：活动已 cancelled（历史半程失败后重跑）且仍有 ENROLLED
        # 报名 → 继续处理剩余报名；无 ENROLLED 则重复取消无意义，拦截
        if a.status == Activity.STATUS_CANCELLED:
            remaining = (
                self.db.query(func.count(ActivityEnrollment.id))
                .filter(
                    ActivityEnrollment.activity_id == activity_id,
                    ActivityEnrollment.status == ActivityEnrollment.STATUS_ENROLLED,
                    ActivityEnrollment.is_deleted == 0,
                )
                .scalar()
            )
            if not remaining:
                raise ValidationError("活动已取消（无待处理报名）")
        else:
            a.status = Activity.STATUS_CANCELLED
        enrollments = (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.activity_id == activity_id,
                ActivityEnrollment.is_deleted == 0,
            )
            .all()
        )
        refund_cnt = cancel_cnt = voided_orders = 0

        refund_amounts: list[Decimal] = []
        from backend.domain.identity.wm10_service import RefundService

        refund_svc = RefundService(self.db)
        for e in enrollments:
            if e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT:
                e.status = ActivityEnrollment.STATUS_CANCELLED
                e.cancel_reason = "活动取消"
                cancel_cnt += 1
                # 2026-10-12 拍板（P4）：**关联订单一并作废**。原先只翻报名状态，订单还挂在
                # "待确认收款/待支付"里——收银台 48 小时内点确认照样能把钱收进来（活动没了、
                # 名额没了、无退款单、无提醒）。条件 UPDATE：只作废"未收款"的单，已收款的不动。
                if e.order_id:
                    voided = (
                        self.db.query(Order)
                        .filter(
                            Order.id == e.order_id,
                            Order.status.in_(
                                [Order.STATUS_PENDING_PAYMENT, Order.STATUS_PENDING_MANUAL]
                            ),
                            Order.paid_at.is_(None),
                        )
                        .update(
                            {Order.status: Order.STATUS_CANCELLED},
                            synchronize_session=False,
                        )
                    )
                    if voided:
                        voided_orders += 1
                        self.db.expire_all()
            elif e.status == ActivityEnrollment.STATUS_ENROLLED:
                if e.order_id:
                    # T16/B-9：付费报名 → 退款待审 + 统一台账 RefundRequest
                    # （活动取消为馆员批量路径，skip_lock_check 不被转让/退会锁阻断）
                    e.status = ActivityEnrollment.STATUS_REFUND_PENDING
                    e.cancel_reason = "活动取消，待退款审核"
                    refund_cnt += 1
                    child = self.db.query(Child).filter(Child.id == e.child_id).first()
                    if child:
                        refund_svc.apply(
                            child,
                            e.order_id,
                            f"活动《{a.title}》取消退款（馆员批量）",
                            skip_lock_check=True,
                            skip_commit=True,  # T31：循环内不提交，尾部统一收口（原子性）
                        )
                    # WM13 触发点4：累计待退金额（关联订单金额，Q6 裁定）
                    o = self.db.query(Order).filter(Order.id == e.order_id).first()
                    if o:
                        refund_amounts.append(Decimal(str(o.amount)))
                else:
                    # 免费报名无款可退：直接取消（与 T14/B-6 对称口径）
                    e.status = ActivityEnrollment.STATUS_CANCELLED
                    e.cancel_reason = "活动取消"
                    cancel_cnt += 1
            # checked_in / refund_pending / refunded / cancelled 不动
        # WM11：活动取消通知已报名家庭（每家庭一条，去重）
        from backend.domain.identity.models import Parent

        parent_ids: set[int] = set()
        for e in enrollments:
            c = self.db.query(Child).filter(Child.id == e.child_id).first()
            if c:
                parent_ids.add(c.parent_id)
        for pid in parent_ids:
            p = self.db.query(Parent).filter(Parent.id == pid).first()
            NotificationService(self.db).send(
                parent_id=pid,
                scene=SCENE_ACTIVITY_CANCEL,
                title="活动取消",
                content=f"《{a.title}》活动已取消，已付费用将进入退款审核流程。",
                category=Notification.CATEGORY_ACTIVITY,
                ref_type="activity",
                ref_id=str(activity_id),
                openid=p.wechat_openid if p else None,
            )
        # WM13 触发点4：活动批量退款汇总 → 管理待办通知（同事务，幂等；Q6：applicant=活动名）
        if refund_cnt > 0:
            from backend.common.admin_notification_models import AdminNotification
            from backend.common.admin_notifications import AdminNotifyService

            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_ACTIVITY_BATCH_REFUND,
                title="【活动退款】",
                content=(
                    f"【活动退款】《{a.title}》已取消，{refund_cnt} 笔报名费待退款审核"
                    + (f"（合计 ￥{sum(refund_amounts)}）" if refund_amounts else "")
                ),
                ref_type=AdminNotification.REF_ACTIVITY,
                ref_id=str(activity_id),
                applicant_name=a.title,
                amount=sum(refund_amounts) if refund_amounts else None,
            )
        # P4：作废订单要给馆员一条待办（"活动没了，这些单别再收钱"）
        if voided_orders:
            from backend.common.admin_notification_models import AdminNotification
            from backend.common.admin_notifications import AdminNotifyService

            AdminNotifyService(self.db).send(
                scene=AdminNotification.SCENE_ACTIVITY_ORDER_VOIDED,
                title="【活动订单作废】",
                content=(
                    f"【活动订单作废】《{a.title}》已取消，{voided_orders} 笔未收款订单已同步作废"
                    "（收银台不要再收这几笔）"
                ),
                ref_type=AdminNotification.REF_ACTIVITY,
                ref_id=str(activity_id),
                applicant_name=a.title,
            )
        publish_audit(
            self.db,
            admin=admin,
            action="activity.cancel",
            target_type="activity",
            target_id=str(activity_id),
            detail={
                "refund_pending": refund_cnt,
                "cancelled": cancel_cnt,
                "orders_voided": voided_orders,
            },
            reason="门店取消活动",
        )
        self.db.commit()
        return {
            "activity_id": activity_id,
            "refund_pending": refund_cnt,
            "cancelled": cancel_cnt,
            "orders_voided": voided_orders,
        }
