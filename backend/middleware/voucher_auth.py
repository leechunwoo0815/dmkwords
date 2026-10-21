# backend/middleware/voucher_auth.py — 凭证查看鉴权（WM3-B2）
"""身份（media_auth 三道校验，P0-F3 同标准）+ 权限（member.manage，**取库里的当前角色**）。

Router 零 try/except 纪律：decode 异常与权限判定集中在此层。
权限语义：凭证是会员域资源，运营专员（staff 含 member.manage）可看，
无需 book.manage（独立端点，不挂 catalog /uploads）。"""

from __future__ import annotations

from backend.common.exceptions import ForbiddenError


def assert_voucher_viewer(request, token: str = "", db=None) -> dict:
    """凭证查看鉴权链；失败抛 UnauthorizedError/ForbiddenError。

    第八轮审查 P2-23：**权限判据取数据库里的当前角色**（`authorize_media` 返回的
    `AdminUser.role`），不再读 JWT 载荷里的 `role`——载荷是**签发时刻的快照**：
    超管被降级成专员（`staff.update`）之后，旧 token 仍带着 `role=superadmin`，
    拿它判权限就是结构性提权面（当时未爆，只因两个角色的权限集恰好都含 member.manage；
    将来把 member.manage 从某角色撤掉时，这条路径会继续放行）。
    """
    from backend.domain.admin.service import role_has_permission
    from backend.domain.catalog.media_auth import authorize_media

    admin = authorize_media(request, token, db)
    if admin is None or not role_has_permission(admin.role, "member.manage"):
        raise ForbiddenError("需要会员管理权限")
    return {"sub": str(admin.id), "role": admin.role}
