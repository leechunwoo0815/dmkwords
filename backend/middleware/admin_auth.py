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
#: `auto_error=False` 版：缺头时返回 None 而不是抛 401——供"头或 query token 双通道"的
#: 下行媒体端点使用（`<img>`/`innerAudioContext` 无法携带头；第十轮 W2-D6）。
security_optional = HTTPBearer(auto_error=False)


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


def validate_admin_payload(db: Session, payload: dict) -> AdminUser:
    """P0-F3：媒体端点完整凭证校验（type 401 + 公共加载/撤销）。

    媒体走独立入口（query token），type 错误按未登录处理（401 语义）。

    **返回值 = 数据库里的当前账号**（第八轮审查 P2-23）：调用方判权限必须用它，
    不能读 token 载荷里的 `role`——载荷是**签发时刻的快照**（详见 voucher_auth）。
    """
    if payload.get("type") != "admin":
        raise UnauthorizedError("非管理端凭证")
    return _load_active_admin(db, payload)


def admin_from_query_token(token: str, db: Session) -> AdminUser:
    """query token → AdminUser（下行媒体端点的第二通道；第十轮 W2-D6，2026-10-25）。

    为什么需要：`/api/admin/uploads/{path}` 原先只认 `Authorization` 头，而同族媒体端点
    （小程序封面/音频/评估图）都提供 `?token=`——因为 `<img src>`、`innerAudioContext`
    这类浏览器/组件发起的下行请求**没法带头**。管理端要直链显示上传图时只能整包下载，
    或者绕到别的端点。这里与 `get_current_admin` 共用同一套加载/撤销判定（`_load_active_admin`
    里含 token_generation 校验），只是入口凭证从"头"换成"query"。
    """
    from backend.common.security import decode_admin_token

    try:
        payload = decode_admin_token(token)
    except jwt.PyJWTError as e:
        raise UnauthorizedError("Token无效或已过期") from e
    return validate_admin_payload(db, payload)


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
