"""RBAC 权限中间件 — require_perm 依赖注入"""

import logging

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.common.exceptions import ForbiddenError
from backend.database import get_db
from backend.domain.admin.models import Admin
from backend.middleware.admin_auth import get_current_admin

logger = logging.getLogger(__name__)


def require_perm(*perm_codes: str):
    """RBAC 权限检查依赖注入

    用法:
        admin=Depends(require_perm("user.create"))
        admin=Depends(require_perm("user.create", "user.edit"))
    """

    def perm_checker(
        admin: Admin = Depends(get_current_admin),
        db: Session = Depends(get_db),
    ) -> Admin:
        from backend.domain.admin.services.account_service import AdminAccountService

        if not any(AdminAccountService(db).has_permission(admin, code) for code in perm_codes):
            logger.warning(
                "Permission denied: admin_id=%d, username=%s, required=%s, role_id=%d",
                admin.id,
                admin.username,
                perm_codes,
                admin.admin_role_id,
            )
            raise ForbiddenError("权限不足")
        return admin

    return perm_checker


def require_super_admin():
    """仅超级管理员可执行的依赖注入（F13：会员状态变更/复活等高权限操作）

    与 require_perm 的区别：super_admin 是角色级判定，不依赖具体权限码——
    staff/teacher 即使被授予 child.edit 也不得执行本类操作。
    """

    def super_checker(
        admin: Admin = Depends(get_current_admin),
        db: Session = Depends(get_db),
    ) -> Admin:
        from backend.domain.admin.services.account_service import AdminAccountService

        if not AdminAccountService(db).is_super_admin(admin):
            logger.warning(
                "Super admin only: admin_id=%d, username=%s, role_code=%s",
                admin.id,
                admin.username,
                AdminAccountService(db).get_role_code(admin),
            )
            raise ForbiddenError("仅超级管理员可执行此操作")
        return admin

    return super_checker
