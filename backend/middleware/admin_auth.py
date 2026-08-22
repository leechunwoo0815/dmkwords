# backend/middleware/admin_auth.py
"""管理员认证中间件

认证方式：HTTP Bearer Token（Authorization header）
管理端全部 API 走 Bearer header，浏览器不会自动附带，因此 CSRF 攻击面不适用。
Cookie 仅在 login 页面写入，用于页面加载时初始 token 同步，未被任何 API 用作认证凭据。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.common.exceptions import ForbiddenError, UnauthorizedError
from backend.config import get_settings
from backend.database import get_db
from backend.domain.admin.models import Admin

logger = logging.getLogger(__name__)
settings = get_settings()
security = HTTPBearer()


def create_admin_token(admin_id: int, role: int, token_generation: int = 0) -> str:
    from backend.common.config_service import ConfigService
    from backend.database import get_session

    db = get_session()()
    try:
        expire_hours = ConfigService.get_int(
            db, "admin_token_expire_hours", settings.ADMIN_TOKEN_EXPIRE_HOURS
        )
    finally:
        db.close()

    expire = datetime.now(UTC) + timedelta(hours=expire_hours)
    payload = {
        "sub": str(admin_id),
        "role": role,
        "exp": expire,
        "type": "admin",
        "jti": str(uuid.uuid4()),
        "gen": token_generation,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Admin:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as e:
        raise UnauthorizedError("Token无效或已过期") from e

    if payload.get("type") != "admin":
        raise ForbiddenError("需要管理员权限")

    admin_id = payload.get("sub")
    if not admin_id:
        raise UnauthorizedError("Token中缺少管理员信息")

    admin = (
        db.query(Admin)
        .filter(
            Admin.id == int(admin_id),
            Admin.is_deleted == 0,
            Admin.status == Admin.STATUS_ACTIVE,
        )
        .first()
    )
    if not admin:
        raise UnauthorizedError("管理员不存在或已禁用")

    # S-03: 校验 token_generation，改密码后旧 Token 立即失效
    token_gen = payload.get("gen", 0)
    if int(token_gen) != admin.token_generation:
        raise UnauthorizedError("Token已失效，请重新登录")

    return admin
