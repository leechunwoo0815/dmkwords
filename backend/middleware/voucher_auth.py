# backend/middleware/voucher_auth.py — 凭证查看鉴权（WM3-B2）
"""身份（media_auth 三道校验，P0-F3 同标准）+ 权限（member.manage 实时派生）。

Router 零 try/except 纪律：decode 异常与权限判定集中在此层。
权限语义：凭证是会员域资源，运营专员（staff 含 member.manage）可看，
无需 book.manage（独立端点，不挂 catalog /uploads）。"""

from __future__ import annotations

from backend.common.exceptions import ForbiddenError
from backend.common.security import decode_admin_token
from backend.domain.admin.service import role_has_permission


def assert_voucher_viewer(request, token: str = "", db=None) -> dict:
    """凭证查看鉴权链；失败抛 UnauthorizedError/ForbiddenError。"""
    from backend.domain.catalog.media_auth import authorize_media

    authorize_media(request, token, db)
    auth = request.headers.get("authorization", "")
    raw = auth[len("Bearer ") :] if auth.startswith("Bearer ") else token
    try:
        payload = decode_admin_token(raw)
    except Exception:  # noqa: BLE001 — authorize_media 已校验，此处兜底防越权放大
        payload = None
    if not payload or not role_has_permission(payload.get("role", ""), "member.manage"):
        raise ForbiddenError("需要会员管理权限")
    return payload
