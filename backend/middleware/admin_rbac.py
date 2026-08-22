# backend/middleware/admin_rbac.py — 声明式 RBAC 依赖注入
"""require_perm：Router 声明权限码，中间件按角色权限目录校验（单一事实源在 admin/service.py）。"""

from __future__ import annotations

import logging

from fastapi import Depends

from backend.common.exceptions import ForbiddenError
from backend.domain.admin.models import AdminUser
from backend.domain.admin.service import role_has_permission
from backend.middleware.admin_auth import get_current_admin

logger = logging.getLogger(__name__)


def require_perm(*perm_codes: str):
    """用法：admin: AdminUser = Depends(require_perm("config.update"))"""

    def perm_checker(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        for code in perm_codes:
            if role_has_permission(admin.role, code):
                return admin
        logger.warning(
            "Permission denied: admin_id=%s username=%s role=%s required=%s",
            admin.id,
            admin.username,
            admin.role,
            perm_codes,
        )
        raise ForbiddenError("权限不足")

    return perm_checker


def require_super_admin():
    """角色级判定：仅超管可执行（资金审核/系统管理类操作）。"""

    def super_checker(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.role != AdminUser.ROLE_SUPER_ADMIN:
            logger.warning(
                "Super admin only: admin_id=%s username=%s role=%s",
                admin.id,
                admin.username,
                admin.role,
            )
            raise ForbiddenError("仅超级管理员可执行此操作")
        return admin

    return super_checker
