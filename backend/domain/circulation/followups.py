# backend/domain/circulation/followups.py — 放行借书后的「会籍跟进」待办
"""馆员**放行**借书后要给运营专员留一条跟进待办（两个人命：未入会跟进入会、过期跟进续费）。

- **未入会**（R-313）：`会员状态为 none` 的孩子临时借书（开关 + 放行原因）→「入会跟进」。
- **已过期**（PRD §3.7，2026-10-14 G11 落地）：原文「馆员和家长沟通后…人工放行当次借书——放行时馆员
  填一个原因，系统记录在案，同时**自动生成一条"续费跟进"提醒**给运营专员，确保这件事有人跟、不遗漏」。

两条都写**同一 scene**（`admin.member_follow_up` + `ref_type=child`），靠标题区分；显示态由
`admin/todo_service._decide_member_followup` 按**有效会员状态**实时推导——家长入会/续费即**自动审结**，
不需要人工销项。

**为什么单独成模块**：`circulation/service.py` 已贴近 800 行 god-file 上限（架构关判红），且两块逻辑
同源（同一 scene、同一幂等纪律），抽出来后两处口径可直接对照，改一处不会漏另一处。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.admin_notification_models import AdminNotification


def send_join_followup(
    db: Session, *, child, book, due_hint: str, override_reason: str | None
) -> bool:
    """未入会临时借书 → 「入会跟进」（幂等）。返回是否新写入。

    幂等：同孩子 `dedup_key` 固定（默认 "1"）→ 多次放行只留一条；
    显示态由 `todo_service` 按会员状态实时推导（一旦入会即自动审结）。
    """
    from backend.common.admin_notifications import AdminNotifyService

    return AdminNotifyService(db).send(
        scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP,
        title="入会跟进",
        content=(
            f"{child.name} 未入会临时借书《{book.title}》（{due_hint}）"
            f"，放行原因：{override_reason}。请跟进家长办理入会。"
        ),
        ref_type=AdminNotification.REF_CHILD,
        ref_id=child.id,
        applicant_name=child.name,
    )


def send_renewal_followup(db: Session, *, child, book, override_reason: str | None) -> bool:
    """过期放行借书 → 「续费跟进」（PRD §3.7）。返回是否新写入。

    幂等按**到期周期**：`dedup_key = renew:{到期日}` —— 同一到期周期重复放行只留一条，
    下一个周期（又是新的一年）会重新提醒。固定 key 会让"第二年又过期"再也提醒不出来。
    仅在**填了放行原因**时调用（真放行才需要人跟；没填原因的那次会被软闸拦下）。
    """
    from backend.common.admin_notifications import AdminNotifyService

    return AdminNotifyService(db).send(
        scene=AdminNotification.SCENE_MEMBER_FOLLOW_UP,
        title="续费跟进",
        content=(
            f"{child.name} 会员已过期，放行借书《{book.title}》"
            f"（放行原因：{override_reason}）。请跟进家长续费。"
        ),
        ref_type=AdminNotification.REF_CHILD,
        ref_id=child.id,
        applicant_name=child.name,
        dedup_key=f"renew:{child.member_expire or 'none'}",
    )
