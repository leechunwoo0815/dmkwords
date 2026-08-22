# backend/domain/admin/service.py — 认证 / 配置中心 / 审计
"""admin 域服务层。

事务纪律：Service 层统一 commit/rollback；审计日志与业务变更同事务写入。
配置缓存：进程内 TTL 缓存（60s），更新同键即失效——单机部署（ADR-005）足够。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from backend.common.config_service import (  # noqa: F401 — 兼容旧引用
    ConfigService,
    invalidate_config_cache,
)
from backend.common.exceptions import (
    ForbiddenError,
    UnauthorizedError,
)
from backend.common.security import create_admin_token, verify_password
from backend.domain.admin.models import AdminUser, AuditLog, SystemConfig
from backend.domain.admin.repository import (
    AdminUserRepository,
    AuditLogRepository,
    SystemConfigRepository,
)

# ---------- 权限目录（声明式 RBAC 的单一事实源） ----------
# superadmin = 全量；staff = 日常运营（借还/图书/活动/会员办理/放行留痕），不含资金审核与系统管理
STAFF_PERMISSIONS = [
    "config.view",
    "book.manage",
    "borrow.operate",
    "member.manage",
    "activity.manage",
    "quiz.manage",
    "audio.manage",
    "dashboard.view",
]
SUPER_ADMIN_PERMISSIONS = ["*"]


def permissions_for_role(role: str) -> list[str]:
    if role == AdminUser.ROLE_SUPER_ADMIN:
        return SUPER_ADMIN_PERMISSIONS
    return STAFF_PERMISSIONS


def role_has_permission(role: str, perm_code: str) -> bool:
    perms = permissions_for_role(role)
    return "*" in perms or perm_code in perms


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = AdminUserRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def login(self, username: str, password: str) -> tuple[str, AdminUser]:
        user = self.user_repo.get_by_username(username)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("用户名或密码错误")
        if user.status != AdminUser.STATUS_ACTIVE:
            raise ForbiddenError("账号已禁用，请联系超级管理员")

        token = create_admin_token(user.id, user.role, user.token_generation)
        user.last_login_at = datetime.now()
        self.user_repo.update(user)
        self.audit_repo.create(
            AuditLog(
                actor_id=user.id,
                actor_name=user.display_name or user.username,
                action=AuditLog.ACTION_LOGIN,
                target_type="admin_user",
                target_id=str(user.id),
                detail=json.dumps(
                    {"username": user.username, "role": user.role}, ensure_ascii=False
                ),
                reason="登录",
            )
        )
        self.db.commit()
        return token, user


class AuditService:
    def __init__(self, db: Session):
        self.db = db
        self.audit_repo = AuditLogRepository(db)

    def list_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        actor_id: int | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditLog], int]:
        return self.audit_repo.list_with_filters(page, page_size, actor_id, action)


class DashboardService:
    """仪表盘运行数据（WM1：全部真实可查；业务指标随模块交付扩展）。"""

    def __init__(self, db: Session):
        self.db = db
        self.user_repo = AdminUserRepository(db)
        self.config_repo = SystemConfigRepository(db)
        self.audit_repo = AuditLogRepository(db)

    def overview(self) -> dict:
        from datetime import datetime

        from sqlalchemy import func

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        admin_count = (
            self.db.query(func.count(AdminUser.id))
            .filter(AdminUser.is_deleted == 0, AdminUser.status == AdminUser.STATUS_ACTIVE)
            .scalar()
            or 0
        )
        today_logins = (
            self.db.query(func.count(AuditLog.id))
            .filter(
                AuditLog.action == AuditLog.ACTION_LOGIN,
                AuditLog.created_at >= today_start,
            )
            .scalar()
            or 0
        )
        config_count = (
            self.db.query(func.count(SystemConfig.id)).filter(SystemConfig.is_deleted == 0).scalar()
            or 0
        )

        recent_changes = (
            self.db.query(AuditLog)
            .filter(
                AuditLog.action == AuditLog.ACTION_CONFIG_UPDATE,
                AuditLog.is_deleted == 0,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(5)
            .all()
        )

        def _fmt(entry: AuditLog) -> dict:
            config = self.config_repo.get_by_key(entry.target_id)
            name = config.display_name or config.description if config else entry.target_id
            try:
                detail = json.loads(entry.detail or "{}")
                change = f"{detail.get('old', '?')} → {detail.get('new', '?')}"
            except (ValueError, TypeError):
                change = entry.detail or ""
            return {
                "config_name": name or entry.target_id,
                "change": change,
                "actor_name": entry.actor_name,
                "created_at": entry.created_at.strftime("%m-%d %H:%M") if entry.created_at else "",
            }

        return {
            "admin_count": admin_count,
            "today_logins": today_logins,
            "config_count": config_count,
            "recent_config_changes": [_fmt(e) for e in recent_changes],
        }
