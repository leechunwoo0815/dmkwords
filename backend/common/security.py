# backend/common/security.py — 密码哈希（stdlib PBKDF2，零额外依赖）+ JWT
"""安全工具：密码哈希与 JWT 签发/校验。

密码：hashlib.pbkdf2_hmac(sha256, 200k 迭代)，存储格式 `pbkdf2_sha256$iterations$salt_hex$hash_hex`。
JWT：PyJWT（HS256），admin token 携带 type=admin + token_generation（改密后旧 token 失效）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

from backend.config import get_settings

_ITERATIONS = 200_000
_ALGO = "HS256"


# ---------- 密码 ----------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        scheme, iterations, salt, hash_hex = hashed.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(digest.hex(), hash_hex)


# ---------- JWT ----------


def create_admin_token(admin_id: int, role: str, token_generation: int = 0) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(hours=settings.ADMIN_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(admin_id),
        "role": role,
        "type": "admin",
        "gen": token_generation,
        "jti": str(uuid.uuid4()),
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGO)


def decode_admin_token(token: str) -> dict:
    """校验并解码 admin token；无效/过期抛 jwt.PyJWTError（调用方转 UnauthorizedError）。"""
    settings = get_settings()
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGO])
