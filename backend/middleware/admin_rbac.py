# backend/middleware/admin_rbac.py — 声明式 RBAC 依赖注入
"""require_perm：Router 声明权限码，中间件按角色权限目录校验（单一事实源在 admin/service.py）。"""

from __future__ import annotations

import logging

from fastapi import Depends, Request

from backend.common.exceptions import ForbiddenError, UnauthorizedError
from backend.database import get_db
from backend.domain.admin.models import AdminUser
from backend.domain.admin.service import role_has_permission
from backend.middleware.admin_auth import (
    admin_from_query_token,
    get_current_admin,
    security_optional,
)

logger = logging.getLogger(__name__)


def require_perm_or_query_token(*perm_codes: str):
    """头或 `?token=` 双通道的权限判据（第十轮 W2-D6，2026-10-25）。

    为什么需要：下行媒体请求（`<img src>`、`innerAudioContext`）带不了 `Authorization`
    头，同族媒体端点都开了 query token；`/api/admin/uploads/{path}` 原先只有头一条通道。
    优先级：**头优先**（主通道，浏览器 fetch 都带头）→ 无头时用 query token。
    两条通道最终都落到同一个 `AdminUser`（含 token_generation 撤销校验），权限判据同一份。
    """

    async def checker(
        request: Request,
        credentials: object = Depends(security_optional),
        db=Depends(get_db),
    ) -> AdminUser:
        if credentials is not None:
            admin = await get_current_admin(credentials, db)
        else:
            qtoken = request.query_params.get("token") or ""
            if not qtoken:
                raise UnauthorizedError("未提供凭证")
            admin = admin_from_query_token(qtoken, db)
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

    return checker


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
