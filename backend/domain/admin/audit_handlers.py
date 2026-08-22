# backend/domain/admin/audit_handlers.py — 订阅业务域审计事件（admin 侧装配）
"""事件总线订阅器：各业务域 publish_audit(...) → 此处落 audit_logs。

注册时机：main.py 启动时 import 本模块（注册副作用）。
"""

from __future__ import annotations

import json
import logging

from backend.common.events import event_bus
from backend.domain.admin.models import AuditLog
from backend.domain.catalog.audit_events import AuditRequestedEvent

logger = logging.getLogger(__name__)


def _handle_audit_requested(event: AuditRequestedEvent, db) -> None:
    if db is None:
        logger.warning("audit.requested 事件缺少 db session，跳过")
        return
    db.add(
        AuditLog(
            actor_id=event.actor_id,
            actor_name=event.actor_name,
            action=event.action,
            target_type=event.target_type,
            target_id=event.target_id,
            detail=json.dumps(event.detail, ensure_ascii=False) if event.detail else None,
            reason=event.reason,
        )
    )
    # 只 add 不 commit：跟随发布方事务（发布方 commit 时一并落库）


def register_audit_handlers() -> None:
    event_bus.subscribe(AuditRequestedEvent.event_type, _handle_audit_requested)
    logger.info("audit handlers registered: %s", AuditRequestedEvent.event_type)
