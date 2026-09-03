# backend/domain/activity/service.py — 活动发布/报名/签到/退款矩阵
"""红线对齐（V1.1 §9）：
- 待支付先占名额（防超卖：活动行锁 + 活跃报名计数）
- 已签到不退；未开始未签到全额退；开始前 2h 关线上；已开始线下协商
- 门店取消 → 已付未签到批量转退款待审，逐单超管审核
事务纪律：Service 统一 commit；留痕走审计事件。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.exceptions import ConflictError, NotFoundError, ValidationError
from backend.common.notification_models import Notification
from backend.common.notifications import (
    SCENE_ACTIVITY_CANCEL,
    SCENE_ACTIVITY_ENROLL,
    SCENE_ACTIVITY_REMIND,
    NotificationService,
)
from backend.domain.catalog.audit_events import publish_audit
from backend.domain.identity.models import Child, Order, Parent

from .models import Activity, ActivityEnrollment


def _ticket_code(activity_id: int, child_id: int) -> str:
    return f"TK{activity_id:05d}{child_id:05d}{uuid.uuid4().hex[:4].upper()}"


class ActivityService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 查询 ----------
    def list_admin(self, status: str | None = None) -> list[dict]:
        q = self.db.query(Activity).filter(Activity.is_deleted == 0)
        if status:
            q = q.filter(Activity.status == status)
        rows = q.order_by(Activity.start_at.desc()).limit(200).all()
        return [self._activity_view(a, with_quota=True) for a in rows]

    def list_upcoming(self, child: Child | None = None) -> list[dict]:
        rows = (
            self.db.query(Activity)
            .filter(
                Activity.is_deleted == 0,
                Activity.status == Activity.STATUS_PUBLISHED,
                Activity.start_at >= datetime.now() - timedelta(hours=2),
            )
            .order_by(Activity.start_at)
            .limit(50)
            .all()
        )
        # R-313 活动列表行：退会不可见"会员专属"（C20）
        if child is not None and child.member_status == Child.MEMBER_WITHDRAWN:
            rows = [a for a in rows if not a.member_only]
        out = []
        my_map = self._my_enrollment_map(child.id) if child else {}
        for a in rows:
            v = self._activity_view(a, with_quota=True)
            v["my_enrollment"] = my_map.get(a.id)
            out.append(v)
        return out

    def detail(self, activity_id: int, child: Child | None = None) -> dict:
        a = self._get(activity_id)
        v = self._activity_view(a, with_quota=True)
        if child:
            v["my_enrollment"] = self._my_enrollment_map(child.id).get(a.id)
        return v

    def _my_enrollment_map(self, child_id: int) -> dict:
        rows = (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.child_id == child_id,
                ActivityEnrollment.is_deleted == 0,
            )
            .all()
        )
        return {r.activity_id: self._enrollment_view(r) for r in rows}

    def _get(self, activity_id: int) -> Activity:
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        return a

    def _quota_used(self, activity_id: int) -> int:
        return (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == activity_id,
                ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )

    def _activity_view(self, a: Activity, with_quota: bool = False) -> dict:
        v = {
            "id": a.id,
            "title": a.title,
            "activity_type": a.activity_type,
            "start_at": str(a.start_at),
            "location": a.location,
            "fee": str(a.fee),
            "fee_display": "免费" if not a.fee else f"{a.fee} 元",
            "member_only": bool(a.member_only),
            "enroll_deadline": str(a.enroll_deadline) if a.enroll_deadline else None,
            "status": a.status,
            "description": a.description,
        }
        if with_quota:
            used = self._quota_used(a.id)
            v["quota_used"] = used
            v["quota_left"] = max(0, a.max_quota - used)
            v["max_quota"] = a.max_quota
            v["full"] = used >= a.max_quota
        return v

    def _enrollment_view(self, r: ActivityEnrollment) -> dict:
        return {
            "id": r.id,
            "activity_id": r.activity_id,
            "child_id": r.child_id,
            "status": r.status,
            "ticket_code": r.ticket_code,
            "checked_in_at": str(r.checked_in_at) if r.checked_in_at else None,
            "created_at": str(r.created_at),
        }

    # ---------- 管理端 ----------
    def create(self, admin, req) -> Activity:
        if req.activity_type not in Activity.TYPE_OPTIONS:
            raise ValidationError("活动类型不正确")
        if req.start_at <= datetime.now():
            raise ValidationError("开始时间必须在未来")
        if req.max_quota <= 0:
            raise ValidationError("名额必须大于 0")
        if req.fee < 0:
            raise ValidationError("费用不能为负（免费填 0）")
        if req.enroll_deadline and req.enroll_deadline > req.start_at:
            raise ValidationError("报名截止不能晚于活动开始")
        a = Activity(
            title=req.title.strip(),
            activity_type=req.activity_type,
            start_at=req.start_at,
            location=(req.location or "").strip(),
            max_quota=req.max_quota,
            fee=req.fee,
            description=req.description,
            member_only=1 if req.member_only else 0,
            enroll_deadline=req.enroll_deadline,
            status=Activity.STATUS_PUBLISHED,
        )
        self.db.add(a)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="activity.create",
            target_type="activity",
            target_id=str(a.id),
            detail={"title": a.title, "quota": a.max_quota, "fee": str(a.fee)},
            reason="活动发布",
        )
        self.db.commit()
        return a

    def cancel_activity(self, admin, activity_id: int) -> dict:
        """取消整场：已付未签到 → 退款待审（逐单人工审）；待支付 → 取消。"""
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # E-5/T17：取消全程锁活动行，与 enroll 活动锁串行化
            .first()
        )
        if a.status != Activity.STATUS_PUBLISHED:
            raise ValidationError("活动状态不可取消")
        a.status = Activity.STATUS_CANCELLED
        enrollments = (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.activity_id == activity_id,
                ActivityEnrollment.is_deleted == 0,
            )
            .all()
        )
        refund_cnt = cancel_cnt = 0
        refund_amounts: list[Decimal] = []
        from backend.domain.identity.wm10_service import RefundService

        refund_svc = RefundService(self.db)
        for e in enrollments:
            if e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT:
                e.status = ActivityEnrollment.STATUS_CANCELLED
                e.cancel_reason = "活动取消"
                cancel_cnt += 1
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
        publish_audit(
            self.db,
            admin=admin,
            action="activity.cancel",
            target_type="activity",
            target_id=str(activity_id),
            detail={"refund_pending": refund_cnt, "cancelled": cancel_cnt},
            reason="门店取消活动",
        )
        self.db.commit()
        return {"activity_id": activity_id, "refund_pending": refund_cnt, "cancelled": cancel_cnt}

    def list_enrollments(self, activity_id: int) -> list[dict]:
        self._get(activity_id)
        rows = (
            self.db.query(ActivityEnrollment, Child)
            .join(Child, ActivityEnrollment.child_id == Child.id)
            .filter(
                ActivityEnrollment.activity_id == activity_id, ActivityEnrollment.is_deleted == 0
            )
            .order_by(ActivityEnrollment.id.desc())
            .all()
        )
        out = []
        for e, child in rows:
            v = self._enrollment_view(e)
            v["child_name"] = child.name
            out.append(v)
        return out

    def signin(self, admin, ticket_code: str) -> dict:
        """扫码/手输入场券签到（记录时间 + 操作人）。E-2/T17：锁定读防并发双扫。"""
        code = (ticket_code or "").strip().upper()
        e = (
            self.db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.ticket_code == code, ActivityEnrollment.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not e:
            raise NotFoundError("入场券不存在（请核对券码）")
        if e.status == ActivityEnrollment.STATUS_CHECKED_IN:
            raise ConflictError("该券已签到过")
        if e.status != ActivityEnrollment.STATUS_ENROLLED:
            raise ValidationError(f"报名状态为 {e.status}，不可签到（未完成收款或已退款）")
        a = self._get(e.activity_id)
        if a.status != Activity.STATUS_PUBLISHED:
            raise ValidationError("活动已取消或结束")
        e.status = ActivityEnrollment.STATUS_CHECKED_IN
        e.checked_in_at = datetime.now()
        e.checked_in_by = admin.id
        publish_audit(
            self.db,
            admin=admin,
            action="activity.signin",
            target_type="enrollment",
            target_id=str(e.id),
            detail={"ticket": code, "child_id": e.child_id, "activity": a.title},
            reason="活动签到",
        )
        self.db.commit()
        return {
            "enrollment_id": e.id,
            "child_id": e.child_id,
            "checked_in_at": str(e.checked_in_at),
        }

    # ---------- 退款审核（超管） ----------
    def list_refund_pending(self) -> list[dict]:
        rows = (
            self.db.query(ActivityEnrollment, Activity, Child)
            .join(Activity, ActivityEnrollment.activity_id == Activity.id)
            .join(Child, ActivityEnrollment.child_id == Child.id)
            .filter(
                ActivityEnrollment.status == ActivityEnrollment.STATUS_REFUND_PENDING,
                ActivityEnrollment.is_deleted == 0,
            )
            .order_by(ActivityEnrollment.id.desc())
            .all()
        )
        out = []
        for e, a, child in rows:
            order = (
                self.db.query(Order).filter(Order.id == e.order_id).first() if e.order_id else None
            )
            out.append(
                {
                    "enrollment_id": e.id,
                    "activity_id": a.id,
                    "activity_title": a.title,
                    "child_id": child.id,
                    "child_name": child.name,
                    "order_id": e.order_id,
                    "amount": str(order.amount) if order else "0",
                    "reason": e.cancel_reason or "",
                    "created_at": str(e.created_at),
                }
            )
        return out

    def review_refund(self, admin, enrollment_id: int, approve: bool, remark: str) -> dict:
        """超管逐单审核（B-9/T16 方案 A）：委托 RefundService.review 走统一七态台账。
        行为变更：approve ≠ 钱已退——rr→approved、e 保持 refund_pending，
        退款 execute 成功后联动翻 refunded（与订单退款语义对齐）；
        reject：rr→rejected + e 恢复 enrolled。"""
        from backend.domain.identity.models import RefundRequest
        from backend.domain.identity.wm10_service import RefundService

        e = (
            self.db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.id == enrollment_id, ActivityEnrollment.is_deleted == 0)
            .with_for_update()
            .populate_existing()  # E-3：锁定读，并发双审串行化
            .first()
        )
        if not e or e.status != ActivityEnrollment.STATUS_REFUND_PENDING:
            raise ValidationError("退款申请不存在或状态不可审")
        rr = (
            self.db.query(RefundRequest)
            .filter(
                RefundRequest.order_id == e.order_id,
                RefundRequest.status == RefundRequest.STATUS_PENDING,
                RefundRequest.is_deleted == 0,
            )
            .first()
        )
        if not rr:
            raise ValidationError("未找到关联的统一退款申请（台账断链，请检查数据）")
        RefundService(self.db).review(admin, rr.id, approve, remark)
        if not approve:
            e.status = ActivityEnrollment.STATUS_ENROLLED
            e.cancel_reason = None
        publish_audit(
            self.db,
            admin=admin,
            action="activity.refund_review",
            target_type="enrollment",
            target_id=str(e.id),
            detail={"approve": approve, "remark": remark, "refund_request_id": rr.id},
            reason=remark or ("退款通过，待执行" if approve else "退款拒绝"),
        )
        self.db.commit()
        # WM13 L2 回写（批次五 #7）：该活动退款全部终态 → 汇总通知审计回写（A3 判定）。
        # T16 后 approve 不减 refund_pending（等 execute 联动）；reject 恢复 enrolled 即减
        from backend.common.admin_notification_models import AdminNotification
        from backend.common.admin_notifications import AdminNotifyService

        remaining = (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == e.activity_id,
                ActivityEnrollment.status == ActivityEnrollment.STATUS_REFUND_PENDING,
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
            or 0
        )
        if remaining == 0:
            AdminNotifyService(self.db).mark_handled(
                ref_type=AdminNotification.REF_ACTIVITY, ref_id=str(e.activity_id), admin=admin
            )
            self.db.commit()
        return {"enrollment_id": e.id, "status": e.status}

    # ---------- 报名（小程序） ----------
    def enroll(self, child: Child, activity_id: int) -> dict:
        # 锁活动行：并发扣减防超卖
        a = (
            self.db.query(Activity)
            .filter(Activity.id == activity_id, Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if not a:
            raise NotFoundError("活动不存在")
        if a.status != Activity.STATUS_PUBLISHED:
            raise ValidationError("活动已取消或结束")
        now = datetime.now()
        if a.start_at <= now:
            raise ValidationError("活动已开始，无法报名")
        if a.enroll_deadline and now > a.enroll_deadline:
            raise ValidationError("报名已截止")
        if a.member_only and not child.is_active_member:
            raise ValidationError("该活动仅限会员报名")
        # E-8/T17：转让/退会冻结期不可报名（对齐借书/预约/测验先例）
        if child.operation_locked:
            raise ValidationError("孩子正在转让/退会审核流程中，报名已冻结")
        # 同活动同孩子唯一（活跃态）
        dup = (
            self.db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == activity_id,
                ActivityEnrollment.child_id == child.id,
                ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )
        if dup:
            raise ConflictError("该孩子已报名此活动（不能重复报名）")
        used = self._quota_used(activity_id)
        if used >= a.max_quota:
            raise ConflictError("名额已满（可到店咨询）")

        fee = a.fee
        order = None
        if fee is not None and fee > 0:
            # 收费活动：先建订单（待人工收款确认）占名额
            from backend.domain.identity.service import OrderService

            order = OrderService(self.db)._create_activity_order(child, a, fee)
            status = ActivityEnrollment.STATUS_PENDING_PAYMENT
        else:
            status = ActivityEnrollment.STATUS_ENROLLED
        e = ActivityEnrollment(
            activity_id=activity_id,
            child_id=child.id,
            order_id=order.id if order else None,
            ticket_code=_ticket_code(activity_id, child.id),
            status=status,
        )
        self.db.add(e)
        self.db.flush()
        # WM11：报名成功通知家长
        NotificationService(self.db).send(
            parent_id=child.parent_id,
            scene=SCENE_ACTIVITY_ENROLL,
            title="报名成功",
            content=f"《{a.title}》报名成功，开始时间 {a.start_at:%Y-%m-%d %H:%M}。",
            category=Notification.CATEGORY_ACTIVITY,
            child_id=child.id,
            ref_type="activity",
            ref_id=str(activity_id),
            dedup_key=str(e.id),
        )
        self.db.commit()
        return {"enrollment": self._enrollment_view(e), "order_id": order.id if order else None}

    def on_activity_order_paid(self, order: Order) -> ActivityEnrollment:
        """收款确认 → 报名转正（identity confirm_payment 联动，同一事务）。"""
        e = (
            self.db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order.id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        if not e:
            raise NotFoundError("该订单没有关联的活动报名")
        if e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT:
            e.status = ActivityEnrollment.STATUS_ENROLLED
        return e

    def cancel_enrollment_by_order(self, order: Order) -> None:
        """订单超时取消 → 活动报名取消释放名额（WM11 僵尸单清理联动）。"""
        e = (
            self.db.query(ActivityEnrollment)
            .filter(ActivityEnrollment.order_id == order.id, ActivityEnrollment.is_deleted == 0)
            .first()
        )
        if e and e.status == ActivityEnrollment.STATUS_PENDING_PAYMENT:
            e.status = ActivityEnrollment.STATUS_CANCELLED
            e.cancel_reason = "订单超时未支付，自动取消"

    def activity_remind(self) -> int:
        """活动开始前 3/2/1/当天 提醒已报名家长（PRD §9.4；每节点一次）。"""
        from backend.common.config_service import ConfigService

        remind_days = [
            int(x)
            for x in ConfigService(self.db).get_value("activity_remind_days", "3,2,1,0").split(",")
            if x.strip() != ""
        ]
        if not remind_days:
            return 0
        now = datetime.now()
        sent = 0
        for days in sorted(set(remind_days)):
            target_day = (now + timedelta(days=days)).date()
            start = datetime.combine(target_day, datetime.min.time())
            acts = (
                self.db.query(Activity)
                .filter(
                    Activity.is_deleted == 0,
                    Activity.status == Activity.STATUS_PUBLISHED,
                    Activity.start_at >= start,
                    Activity.start_at < start + timedelta(days=1),
                )
                .all()
            )
            for a in acts:
                enrolls = (
                    self.db.query(ActivityEnrollment)
                    .filter(
                        ActivityEnrollment.activity_id == a.id,
                        ActivityEnrollment.status == ActivityEnrollment.STATUS_ENROLLED,
                        ActivityEnrollment.is_deleted == 0,
                    )
                    .all()
                )
                for e in enrolls:
                    c = self.db.query(Child).filter(Child.id == e.child_id).first()
                    if not c:
                        continue
                    parent = self.db.query(Parent).filter(Parent.id == c.parent_id).first()
                    label = "今天" if days == 0 else f"{days} 天后"
                    if NotificationService(self.db).send(
                        parent_id=c.parent_id,
                        scene=SCENE_ACTIVITY_REMIND,
                        title="活动提醒",
                        content=(
                            f"《{a.title}》将于{label}（{a.start_at:%Y-%m-%d %H:%M}）开始，"
                            f"地点：{a.location}，请提前到场。"
                        ),
                        category=Notification.CATEGORY_ACTIVITY,
                        child_id=c.id,
                        ref_type="activity",
                        ref_id=str(a.id),
                        dedup_key=str(days),
                        openid=parent.wechat_openid if parent else None,
                    ):
                        sent += 1
        if sent:
            self.db.commit()
        return sent

    def activity_auto_finish(self) -> int:
        """已开始超过 1 天且无进行中报名 → finished（活动状态机收口）。"""
        cutoff = datetime.now() - timedelta(days=1)
        acts = (
            self.db.query(Activity)
            .filter(
                Activity.is_deleted == 0,
                Activity.status == Activity.STATUS_PUBLISHED,
                Activity.start_at < cutoff,
            )
            .all()
        )
        finished = 0
        for a in acts:
            active_cnt = (
                self.db.query(func.count(ActivityEnrollment.id))
                .filter(
                    ActivityEnrollment.activity_id == a.id,
                    ActivityEnrollment.status.in_(ActivityEnrollment.ACTIVE_STATUSES),
                    ActivityEnrollment.is_deleted == 0,
                )
                .scalar()
            )
            if active_cnt == 0:
                a.status = Activity.STATUS_FINISHED
                finished += 1
        if finished:
            self.db.commit()
        return finished

    def my_enrollments(self, child: Child) -> list[dict]:
        rows = (
            self.db.query(ActivityEnrollment, Activity)
            .join(Activity, ActivityEnrollment.activity_id == Activity.id)
            .filter(ActivityEnrollment.child_id == child.id, ActivityEnrollment.is_deleted == 0)
            .order_by(ActivityEnrollment.id.desc())
            .all()
        )
        out = []
        for e, a in rows:
            v = self._enrollment_view(e)
            v["activity_title"] = a.title
            v["activity_start_at"] = str(a.start_at)
            v["activity_status"] = a.status
            out.append(v)
        return out

    def cancel(self, child: Child, enrollment_id: int) -> dict:
        """免费活动报名取消（开始前）。付费活动必须走退款矩阵（B-6/T14 守卫）。"""
        e = self._my_enrollment(child, enrollment_id)
        if e.status != ActivityEnrollment.STATUS_ENROLLED:
            raise ValidationError(f"状态 {e.status} 不可取消")
        a = self._get(e.activity_id)
        if (a.fee or 0) > 0 or e.order_id:
            raise ValidationError("付费活动请走退款流程（报名费原路退回）")
        if a.start_at <= datetime.now():
            raise ValidationError("活动已开始，不能取消（请联系馆员）")
        e.status = ActivityEnrollment.STATUS_CANCELLED
        e.cancel_reason = "家长取消"
        self.db.commit()
        return {"enrollment_id": e.id, "status": e.status}

    def apply_refund(self, child: Child, enrollment_id: int) -> dict:
        """退款矩阵（V1.1 §9.3）：已签到不退；未开始未签到全额；前 2h 关线上；已开始线下。
        B-9/T16：委托统一七态退款台账（RefundService.apply），金额 R-309 同源。"""
        e = self._my_enrollment(child, enrollment_id)
        if e.status == ActivityEnrollment.STATUS_CHECKED_IN:
            raise ValidationError("已签到，不能退款（人都来了，成本已发生）")
        if e.status != ActivityEnrollment.STATUS_ENROLLED:
            raise ValidationError(f"状态 {e.status} 不可申请退款")
        a = self._get(e.activity_id)
        if (a.fee or 0) <= 0 or not e.order_id:
            # 与 T14 对称：cancel 仅免费、refund 仅付费
            raise ValidationError("免费活动无需退款，请取消报名")
        now = datetime.now()
        if a.start_at <= now:
            raise ValidationError("活动已开始，请线下与馆员协商处理")
        # 域H M-6+M-1（T29 处置）：activity_refund_cutoff_hours 死配置接线
        # （原硬编码 2h），配置默认 2 与 PRD §9.3 一致
        from backend.common.config_service import ConfigService

        cutoff_hours = int(ConfigService(self.db).get_value("activity_refund_cutoff_hours", "2"))
        if a.start_at - now < timedelta(hours=cutoff_hours):
            raise ValidationError("距开始不足 2 小时，线上退款已关闭，请找馆员线下处理")
        e.status = ActivityEnrollment.STATUS_REFUND_PENDING
        e.cancel_reason = "家长申请退款（活动未开始）"
        # 统一台账：RefundRequest(pending) + order.refund_status=pending + 管理待办通知
        from backend.domain.identity.wm10_service import RefundService

        RefundService(self.db).apply(child, e.order_id, f"活动《{a.title}》报名退款")
        self.db.commit()
        return {"enrollment_id": e.id, "status": e.status, "amount_hint": "全额待审核"}

    def _my_enrollment(self, child: Child, enrollment_id: int) -> ActivityEnrollment:
        e = (
            self.db.query(ActivityEnrollment)
            .filter(
                ActivityEnrollment.id == enrollment_id,
                ActivityEnrollment.child_id == child.id,
                ActivityEnrollment.is_deleted == 0,
            )
            .first()
        )
        if not e:
            raise NotFoundError("报名不存在")
        return e
