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


def create_admin_token(
    admin_id: int, role: str, token_generation: int = 0, expire_hours: int | None = None
) -> str:
    """签发后台 token。

    `expire_hours`（P2-28，2026-10-17 第六十九批）：配置中心 `admin_token_expire_hours`
    的**生效点**。不传或非法（<=0）时回落 `settings.ADMIN_TOKEN_EXPIRE_HOURS`——
    取值/校验策略留在调用方（`AuthService._token_expire_hours`），安全层不读库。
    """
    settings = get_settings()
    hours = expire_hours if expire_hours and expire_hours > 0 else settings.ADMIN_TOKEN_EXPIRE_HOURS
    expire = datetime.now(UTC) + timedelta(hours=hours)
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


_DUMMY_HASH: str | None = None


def dummy_password_hash() -> str:
    """惰性生成的假哈希（与真实哈希同格式同代价）。

    用途：登录时"用户不存在"路径也要跑一次等价校验，否则响应时间暴露账号是否存在
    （P2-11，2026-10-09 审查：原先未知用户名短路返回，实测可区分）。
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-timing-only")
    return _DUMMY_HASH


# ---------- 验证码（第八轮审查 P2-21） ----------

#: 短信验证码位数（6 位数字 = 10^6 空间，配服务端"同一手机号 5 次尝试上限"）
SMS_CODE_DIGITS = 6


def random_digit_code(digits: int = SMS_CODE_DIGITS) -> str:
    """**密码学安全**的数字验证码（全项目唯一生成口径）。

    为什么不能用 `random.randint`（P2-21 实测三处 + 一处死代码都在用）：
    `random` 是 Mersenne Twister，624 个 32 位输出即可**反推内部状态**并预测后续全部输出。
    验证码是家长登录主通道（免密登录），同一进程连续签发多枚码时，攻击者拿几枚自己手机收到的码
    就可能推出**别人**正在等的码——"服务端 5 次尝试上限"只挡暴力枚举，挡不住预测。
    生成一律走 `secrets`（操作系统 CSPRNG）。
    """
    if digits < 1:
        raise ValueError("digits 必须 ≥ 1")
    return f"{secrets.randbelow(10**digits):0{digits}d}"
