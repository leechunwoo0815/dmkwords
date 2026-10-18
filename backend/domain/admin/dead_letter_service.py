# backend/domain/admin/dead_letter_service.py — 死信读取面（P2-1，2026-10-18 第七十八批）
"""为什么单开：`common/events.py` 只负责**落库**，看板读取/销项属管理域；放 `service.py` 会顶到
god-file 上限（同类先例：`task_specs.py` / `todo_service.py`）。

死信是什么：事件处理器重试后仍失败 → 跨域副作用（积分/词数/通知/审计）**没发生**。
落库是第六十九批 P2-18 做的；**读取面一直缺**（第七轮 P2-1）：没人能看见"有多少条待处理"，
等于静默数据缺失。本模块提供列表 + 人工销项（与 `TaskRunLog` 看板同款语义）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.notification_models import DeadLetter


class DeadLetterService:
    """死信看板（列表 / 待处理计数 / 人工销项）。"""

    def __init__(self, db: Session):
        self.db = db

    def list(self, *, page: int = 1, page_size: int = 20, unresolved_only: bool = False) -> dict:
        """分页列表 + **待处理计数**（徽标口径：`handled_at IS NULL`）。

        按 id 倒序（= 最近落库优先；`dead_letters` 无 `created_at` 索引，id 即时间序）。
        """
        q = self.db.query(DeadLetter).filter(DeadLetter.is_deleted == 0)
        pending_q = q.filter(DeadLetter.handled_at.is_(None))
        pending = pending_q.count()
        if unresolved_only:
            q = pending_q
        total = q.count()
        rows = (
            q.order_by(DeadLetter.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
        )
        return {
            "items": [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "handler_name": r.handler_name,
                    "error": (r.error or "")[:500],
                    "payload": (r.payload or "")[:500],
                    "retry_count": r.retry_count,
                    "create_time": r.create_time.isoformat() if r.create_time else None,
                    "handled_at": r.handled_at.isoformat() if r.handled_at else None,
                    "handled_by": r.handled_by,
                }
                for r in rows
            ],
            "total": total,
            "pending": pending,
            "page": page,
            "page_size": page_size,
        }

    def handle(self, dead_letter_id: int, admin) -> dict:
        """人工销项：标记已处理（幂等——已处理的不改首处理人与时刻）。"""
        from backend.common.exceptions import NotFoundError

        row = (
            self.db.query(DeadLetter)
            .filter(DeadLetter.id == dead_letter_id, DeadLetter.is_deleted == 0)
            .first()
        )
        if row is None:
            raise NotFoundError(f"死信不存在: {dead_letter_id}")
        already = row.handled_at is not None
        if not already:
            row.handled_at = datetime.now()
            row.handled_by = admin.id if admin is not None else None
            from backend.domain.catalog.audit_events import publish_audit

            publish_audit(
                self.db,
                admin=admin,
                action="dead_letter.handle",
                target_type="dead_letter",
                target_id=str(row.id),
                detail={"event_type": row.event_type, "handler_name": row.handler_name},
                reason="死信看板人工销项",
            )
        self.db.commit()
        return {"id": row.id, "handled": True, "already_handled": already}
