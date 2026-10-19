# backend/domain/activity/enrollment_ops.py — 报名状态的对偶操作（退款终态联动）
"""第八轮审查 P1-8（2026-10-19）：**报名退款被拒后把报名恢复 `enrolled`**。

为什么单开模块：`wm10_service.py` 已 784/800 行（架构关 god-file 上限，加不下），
而这对操作与 `wm10_service._refund_activity_enrollment`（成功路径 → `refunded`）是**对偶**，
放在一起才好对照——一个在 identity 侧（成功联动），一个在 activity 侧（拒绝回退）。

**缺陷背景**：原先只有 deprecated 的 `POST /api/admin/activity-refunds/{eid}/review` 会恢复报名，
而管理端退款中心只调统一审核端点（`/api/admin/refund-requests/{id}/review`）→ 拒绝后报名
**永久卡 `refund_pending`**：占名额、签到被拒、不能重报（409）、不能再次申请退款、
`activity_batch_refund` 待办永不销项、有活跃报名的活动永不自动 `finished`——只能改库恢复。
"""

from __future__ import annotations

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session


def restore_enrollment_after_refund_rejected(db: Session, order) -> int:
    """把该订单对应报名的 `refund_pending` 回退成 `enrolled`；返回受影响行数（0/1）。

    用**条件 UPDATE**（CAS）而不是"读-改-写"：签到（`ActivityService.signin` 持行锁）、
    取消、再次申请退款都可能并发改这一行，条件写保证只回退"仍是退款中"的那一态，
    不会把并发推进到的其它状态覆盖回去（与 `overdue_list` / 还书的守卫写同款口径）。
    """
    from backend.domain.activity.models import ActivityEnrollment

    result = db.execute(
        sa_update(ActivityEnrollment)
        .where(
            ActivityEnrollment.order_id == order.id,
            ActivityEnrollment.is_deleted == 0,
            ActivityEnrollment.status == ActivityEnrollment.STATUS_REFUND_PENDING,
        )
        .values(status=ActivityEnrollment.STATUS_ENROLLED, cancel_reason=None)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
