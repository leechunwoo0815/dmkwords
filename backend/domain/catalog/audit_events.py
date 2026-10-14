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


def publish_rejection_audit(
    *,
    actor,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict | None = None,
    reason: str = "",
) -> None:
    """**拒绝路径专用**审计留痕（2026-10-14 第五十九批 G13）。

    为什么不能走 `publish_audit`（同事务）：拒绝就抛异常 → 请求事务**必然回滚**，而本模块
    顶部注释已写明审计"跟随发布方事务、失败整体回滚"→ 同事务写的"违规尝试"留痕会一起丢。
    故这里仍然经 EventBus 发布（保持"业务域不反向依赖 admin"的架构约束，落库仍在 admin 订阅器），
    但**传入一个独立会话并立即提交** —— 留痕因此不受主事务回滚影响。

    actor 允许为 None / 无 id（系统自动流转）→ 落 actor_id=0 + actor_name="系统"。
    """
    from backend.database import get_session

    with get_session() as db:
        event_bus.publish(
            AuditRequestedEvent(
                actor_id=int(getattr(actor, "id", 0) or 0),
                actor_name=(
                    getattr(actor, "display_name", "") or getattr(actor, "username", "") or "系统"
                ),
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                detail=detail,
                reason=reason,
            ),
            db=db,
        )
        db.commit()
