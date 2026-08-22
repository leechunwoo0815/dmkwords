# backend/middleware/admin_auth.py — 管理端认证（Bearer JWT）
"""管理端全部 API 走 Authorization: Bearer <token>（浏览器不自动附带，CSRF 攻击面不适用）。"""

from __future__ import annotations

import logging

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.common.exceptions import ForbiddenError, UnauthorizedError
from backend.database import get_db
from backend.domain.admin.models import AdminUser

logger = logging.getLogger(__name__)
security = HTTPBearer()


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> AdminUser:
    token = credentials.credentials
    try:
        from backend.common.security import decode_admin_token

        payload = decode_admin_token(token)
    except jwt.PyJWTError as e:
        raise UnauthorizedError("Token无效或已过期") from e

    if payload.get("type") != "admin":
        raise ForbiddenError("需要管理员权限")

    admin_id = payload.get("sub")
    if not admin_id:
        raise UnauthorizedError("Token中缺少管理员信息")

    admin = (
        db.query(AdminUser)
        .filter(
            AdminUser.id == int(admin_id),
            AdminUser.is_deleted == 0,
            AdminUser.status == AdminUser.STATUS_ACTIVE,
        )
        .first()
    )
    if not admin:
        raise UnauthorizedError("管理员不存在或已禁用")

    # token_generation 不一致 = 改密/禁用后签发的旧 token，立即失效
    if int(payload.get("gen", 0)) != admin.token_generation:
        raise UnauthorizedError("Token已失效，请重新登录")

    return admin
