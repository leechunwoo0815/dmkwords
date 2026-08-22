# backend/domain/catalog/audit_events.py — catalog 域审计事件（跨域经 EventBus，admin 域订阅）
"""业务域不反向依赖 admin（宪法四）：catalog 的审计留痕走事件发布。

事件流：catalog service → AuditRequestedEvent → admin.audit_handlers 落库。
共享同一事务（publish(db=session)），失败整体回滚。
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.common.events import DomainEvent, event_bus


@dataclass
class AuditRequestedEvent(DomainEvent):
    """通用审计请求事件（actor 上下文 + 动作 + 对象 + 详情 + 原因）。"""

    event_type: str = "audit.requested"
    actor_id: int = 0
    actor_name: str = ""
    action: str = ""
    target_type: str = ""
    target_id: str = ""
    detail: dict | None = None
    reason: str = ""


def publish_audit(
    db,
    *,
    admin,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict | None = None,
    reason: str = "",
) -> None:
    event_bus.publish(
        AuditRequestedEvent(
            actor_id=admin.id,
            actor_name=admin.display_name or admin.username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            reason=reason,
        ),
        db=db,
    )
