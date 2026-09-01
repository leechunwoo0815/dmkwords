# backend/domain/catalog/media_auth.py — 管理端媒体资源鉴权（C25）
"""Router 零异常处理纪律：try/except 与异常抛出集中在此层。
支持 Authorization Bearer（后台 fetch）与 query token（<img>/<audio> 无法带头）。

P0-F3（20260831 审查）：家长 token 与 admin token 同密钥同算法签发，只验签名可被
type=parent 的 token 越权拉媒体——补 token type 校验 + token_generation 撤销校验
（与 middleware/admin_auth.get_current_admin 同一标准）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.common.exceptions import UnauthorizedError
from backend.common.security import decode_admin_token


def authorize_media(request, token: str = "", db: Session | None = None) -> None:
    """校验 admin JWT（header 或 query）；失败抛 UnauthorizedError（→401）。

    db 提供时执行完整校验（type + 账号状态 + token_generation 撤销）。
    """
    header = request.headers.get("authorization", "")
    auth_token = header[len("Bearer ") :] if header.startswith("Bearer ") else token
    if not auth_token:
        raise UnauthorizedError("未登录")
    try:
        payload = decode_admin_token(auth_token)
    except Exception:  # noqa: BLE001 — jwt 解析异常统一匿名
        payload = None
    if not payload:
        raise UnauthorizedError("登录已过期，请重新登录")
    # P0-F3：只验签名不够——家长 token 同密钥可伪造通过，必须校验 type
    # P0-F3：完整校验（type + 账号状态 + gen 撤销）委托 middleware（域不依赖 admin）
    from backend.middleware.admin_auth import validate_admin_payload

    validate_admin_payload(db, payload)
