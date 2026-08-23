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
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.common.security import create_admin_token, hash_password, verify_password
from backend.domain.admin.models import AdminUser, AuditLog, SystemConfig
from backend.domain.admin.repository import (
    AdminUserRepository,
    AuditLogRepository,
    SystemConfigRepository,
)
from backend.domain.catalog.audit_events import publish_audit

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


class StaffService:
    """员工账号管理（WM1 超管职责：创建/禁用/改角色/重置密码）。"""

    def __init__(self, db: Session):
        self.db = db
        self.user_repo = AdminUserRepository(db)

    def list(self) -> list[AdminUser]:
        return (
            self.db.query(AdminUser)
            .filter(AdminUser.is_deleted == 0)
            .order_by(AdminUser.id.asc())
            .all()
        )

    def create(
        self, admin: AdminUser, username: str, password: str, display_name: str, role: str
    ) -> AdminUser:
        if self.user_repo.get_by_username(username):
            raise ConflictError("用户名已存在")
        if role not in (AdminUser.ROLE_SUPER_ADMIN, AdminUser.ROLE_STAFF):
            raise ValidationError("角色必须是 superadmin 或 staff")
        user = AdminUser(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            role=role,
            status=AdminUser.STATUS_ACTIVE,
        )
        self.db.add(user)
        self.db.flush()
        publish_audit(
            self.db,
            admin=admin,
            action="staff.create",
            target_type="admin_user",
            target_id=str(user.id),
            detail={"username": username, "role": role},
            reason=f"创建员工账号 {username}",
        )
        self.db.commit()
        return user

    def _get(self, admin: AdminUser, user_id: int) -> AdminUser:
        user = (
            self.db.query(AdminUser)
            .filter(AdminUser.id == user_id, AdminUser.is_deleted == 0)
            .first()
        )
        if not user:
            raise NotFoundError("员工不存在")
        return user

    def update(
        self, admin: AdminUser, user_id: int, display_name: str | None, role: str | None
    ) -> AdminUser:
        user = self._get(admin, user_id)
        if user.id == admin.id and role and role != user.role:
            raise ValidationError("不能修改自己的角色（防自杀锁）")
        if role is not None:
            if role not in (AdminUser.ROLE_SUPER_ADMIN, AdminUser.ROLE_STAFF):
                raise ValidationError("角色必须是 superadmin 或 staff")
            user.role = role
        if display_name is not None:
            user.display_name = display_name
        publish_audit(
            self.db,
            admin=admin,
            action="staff.update",
            target_type="admin_user",
            target_id=str(user.id),
            detail={"display_name": display_name, "role": role},
            reason=f"更新员工账号（id={user.id}）",
        )
        self.db.commit()
        return user

    def set_status(self, admin: AdminUser, user_id: int, status: int) -> AdminUser:
        user = self._get(admin, user_id)
        if user.id == admin.id:
            raise ValidationError("不能禁用自己（防自杀锁）")
        user.status = AdminUser.STATUS_ACTIVE if status else AdminUser.STATUS_DISABLED
        publish_audit(
            self.db,
            admin=admin,
            action="staff.status",
            target_type="admin_user",
            target_id=str(user.id),
            detail={"status": status},
            reason=f"{'启用' if status else '禁用'}员工账号（{user.username}）",
        )
        self.db.commit()
        return user

    def reset_password(self, admin: AdminUser, user_id: int, new_password: str) -> None:
        user = self._get(admin, user_id)
        user.password_hash = hash_password(new_password)
        publish_audit(
            self.db,
            admin=admin,
            action="staff.reset_password",
            target_type="admin_user",
            target_id=str(user.id),
            detail={"username": user.username},
            reason=f"重置员工密码（{user.username}）",
        )
        self.db.commit()
