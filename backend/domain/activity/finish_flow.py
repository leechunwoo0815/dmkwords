# backend/domain/activity/finish_flow.py — 活动自动收口与结束通知（2026-11-03）
"""活动到点收口：status → finished + 给活跃报名家长发站内信。

为什么单独成模块：`activity/service.py` 触发架构关的 800 行上限（god file），
本流程是「定时任务编排」，与报名/签到/退款的服务方法无关，按 `refund_rules.py`
先例抽出；service 只留一行委托。

口径（2026-11-03 甲方报障后修正）：
- 时间：签到窗关闭（`activity_end_at + 30min`）后收口。旧实现用 `start+24h`，
  活动结束后还要多挂近一天；
- 旧实现还要求「无任何活跃报名」才结束——只要有人报名（enrolled/checked_in），
  活动就永远停在 published，后台一直显示「报名中」、签到窗关闭后又刷不了码；
- 现在 enrolled / checked_in 不阻止收口；pending_payment / refund_pending
  （资金在途）仍不结束（pending 有 48h 订单超时兜底，不会永久卡住）；
- 结束时给活跃报名家长发「活动已结束」站内信（每人一条 dedup，重跑不重发）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.notification_models import Notification
from backend.common.notifications import SCENE_ACTIVITY_FINISH, NotificationService
from backend.domain.activity.models import Activity, ActivityEnrollment
from backend.domain.identity.models import Child


def auto_finish_activities(db: Session) -> int:
    """扫描并收口已过签到窗的活动；返回本次结束数。"""
    # 延迟导入：避免 service ↔ finish_flow 的模块级环（service 在方法内导入本模块）。
    from backend.domain.activity.service import CHECKIN_CLOSE_AFTER, activity_end_at

    now = datetime.now()
    acts = (
        db.query(Activity)
        .filter(Activity.is_deleted == 0, Activity.status == Activity.STATUS_PUBLISHED)
        .all()
    )
    finished = 0
    for a in acts:
        if activity_end_at(a) + CHECKIN_CLOSE_AFTER >= now:
            continue
        # B3（2026-10-31 第一百五十一批）：扫描（MVCC 普通读）与写入之间，cancel_activity
        # （锁活动行）可能已把状态推进为 cancelled——盲写 `a.status=finished` 的 UPDATE
        # WHERE 只有 id，会把 cancelled 覆盖成 finished。写入前重锁重读：
        # 活动行锁同时把 enroll（报名也锁活动行）挡在外面。
        fresh = (
            db.query(Activity)
            .filter(Activity.id == a.id, Activity.is_deleted == 0)
            .with_for_update()
            .populate_existing()
            .first()
        )
        if fresh is None or fresh.status != Activity.STATUS_PUBLISHED:
            continue
        money_in_flight = (
            db.query(func.count(ActivityEnrollment.id))
            .filter(
                ActivityEnrollment.activity_id == a.id,
                ActivityEnrollment.status.in_(
                    (
                        ActivityEnrollment.STATUS_PENDING_PAYMENT,
                        ActivityEnrollment.STATUS_REFUND_PENDING,
                    )
                ),
                ActivityEnrollment.is_deleted == 0,
            )
            .scalar()
        )
        if money_in_flight:
            continue
        fresh.assert_transition(Activity.STATUS_FINISHED)
        fresh.status = Activity.STATUS_FINISHED
        finished += 1
        notify_activity_finished(db, fresh)
    if finished:
        db.commit()
    return finished


def notify_activity_finished(db: Session, a: Activity) -> None:
    """活动结束站内信：只发仍活跃（enrolled / checked_in）的报名家长。

    微信模板未映射 `activity.finish` → 如实降级为仅站内消息（不伪装成功）。
    dedup_key 按 enrollment，重跑不重复发；与报名成功通知（scene 不同）互不覆盖。
    """
    from backend.domain.activity.service import activity_end_at
    from backend.domain.identity.wm_notify import parent_openid

    rows = (
        db.query(ActivityEnrollment)
        .filter(
            ActivityEnrollment.activity_id == a.id,
            ActivityEnrollment.status.in_(
                (ActivityEnrollment.STATUS_ENROLLED, ActivityEnrollment.STATUS_CHECKED_IN)
            ),
            ActivityEnrollment.is_deleted == 0,
        )
        .all()
    )
    end_at = activity_end_at(a)
    for e in rows:
        child = db.query(Child).filter(Child.id == e.child_id).first()
        if child is None:
            continue
        NotificationService(db).send(
            parent_id=child.parent_id,
            scene=SCENE_ACTIVITY_FINISH,
            title="活动已结束",
            content=f"《{a.title}》已于 {end_at:%Y-%m-%d %H:%M} 结束。",
            category=Notification.CATEGORY_ACTIVITY,
            child_id=e.child_id,
            ref_type="activity",
            ref_id=str(a.id),
            dedup_key=f"finished:{e.id}",
            openid=parent_openid(db, child.parent_id),
        )
