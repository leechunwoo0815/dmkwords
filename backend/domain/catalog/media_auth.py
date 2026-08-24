# backend/domain/catalog/media_auth.py — 管理端媒体资源鉴权（C25）
"""Router 零异常处理纪律：try/except 与异常抛出集中在此层。
支持 Authorization Bearer（后台 fetch）与 query token（<img>/<audio> 无法带头）。
"""

from __future__ import annotations

from backend.common.exceptions import UnauthorizedError
from backend.common.security import decode_admin_token


def authorize_media(request, token: str = "") -> None:
    """校验 admin JWT（header 或 query）；失败抛 UnauthorizedError（→401）。"""
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
