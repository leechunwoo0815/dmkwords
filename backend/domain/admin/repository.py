# backend/domain/admin/repository.py — admin 域数据访问
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.common.base_repo import BaseRepository
from backend.domain.admin.models import AdminUser, AuditLog, SystemConfig


class AdminUserRepository(BaseRepository[AdminUser]):
    def __init__(self, db: Session):
        super().__init__(db, AdminUser)

    def get_by_username(self, username: str) -> AdminUser | None:
        return self.get_by_field("username", username)


class SystemConfigRepository(BaseRepository[SystemConfig]):
    def __init__(self, db: Session):
        super().__init__(db, SystemConfig)

    def get_by_key(self, key: str) -> SystemConfig | None:
        return self.get_by_field("config_key", key)


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, db: Session):
        super().__init__(db, AuditLog)

    def list_with_filters(
        self,
        page: int,
        page_size: int,
        actor_id: int | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditLog], int]:
        q = self.db.query(AuditLog).filter(AuditLog.is_deleted == 0)
        if actor_id is not None:
            q = q.filter(AuditLog.actor_id == actor_id)
        if action:
            q = q.filter(AuditLog.action == action)
        total = q.count()
        items = (
            q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

    def count_by_action(self, action: str) -> int:
        return (
            self.db.query(func.count(AuditLog.id))
            .filter(AuditLog.is_deleted == 0, AuditLog.action == action)
            .scalar()
            or 0
        )
