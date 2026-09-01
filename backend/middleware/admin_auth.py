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


def _load_active_admin(db: Session, payload: dict) -> AdminUser:
    """公共凭证校验（账号存在/active + token_generation 撤销），返回 AdminUser。

    type 校验留各自入口（get_current_admin=403 有身份无权限；媒体=401 匿名入口语义）。
    """
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
        raise UnauthorizedError("登录状态已失效，请重新登录")
    return admin


def validate_admin_payload(db: Session, payload: dict) -> None:
    """P0-F3：媒体端点完整凭证校验（type 401 + 公共加载/撤销）。

    媒体走独立入口（query token），type 错误按未登录处理（401 语义）。
    """
    if payload.get("type") != "admin":
        raise UnauthorizedError("非管理端凭证")
    _load_active_admin(db, payload)


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

    return _load_active_admin(db, payload)
