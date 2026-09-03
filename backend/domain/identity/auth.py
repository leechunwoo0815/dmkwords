# backend/domain/identity/auth.py — 小程序家长鉴权统一（A-1/T6 下沉 20260903）
"""家长 token 签发/解析、当前家长依赖、孩子归属校验、家长登录。
从各域 miniapp_router 下沉（架构门禁扩 miniapp_router 后 Router 零违规）。

query-token 鉴权：音频/图片等组件无法携带 Authorization 头时用。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, Header

from backend.common.exceptions import UnauthorizedError, ValidationError
from backend.config import get_settings
from backend.database import get_db
from backend.domain.identity.models import Child, Parent


def _parent_token(parent_id: int) -> str:
    import jwt as pyjwt

    payload = {
        "sub": str(parent_id),
        "type": "parent",
        "exp": datetime.now(UTC) + timedelta(days=30),
    }
    return pyjwt.encode(payload, get_settings().SECRET_KEY, algorithm="HS256")


def _parent_from_token(token: str, db) -> Parent:
    import jwt as pyjwt

    try:
        payload = pyjwt.decode(token, get_settings().SECRET_KEY, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        raise UnauthorizedError("请先登录") from e
    if payload.get("type") != "parent":
        raise UnauthorizedError("无效的家长凭证")
    parent = (
        db.query(Parent).filter(Parent.id == int(payload["sub"]), Parent.is_deleted == 0).first()
    )
    if not parent:
        raise UnauthorizedError("账号不存在")
    return parent


def get_current_parent(authorization: str = Header(...), db=Depends(get_db)) -> tuple[Parent, Any]:
    """FastAPI 依赖：解析家长 token，返回 (Parent, Session)。"""
    token = authorization.replace("Bearer ", "")
    return _parent_from_token(token, db), db


def child_of_parent(db, parent_id: int, child_id: int) -> Child:
    """孩子归属校验（P0-F1 防越权）：查孩子且属该家长，否则 422。"""
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


def authenticate_parent(db, phone: str, code: str) -> dict:
    """家长登录（A-1/T6 下沉）：校验验证码 + 查家长 + 返回 token 与孩子列表。
    P0-F2 fail-closed：LOGIN_DEV_CODE 置空时任何 code 全拒（生产禁用固定验证码）。"""
    dev_code = get_settings().LOGIN_DEV_CODE
    if not dev_code or code != dev_code:
        raise ValidationError("验证码错误")
    parent = db.query(Parent).filter(Parent.phone == phone, Parent.is_deleted == 0).first()
    if not parent:
        raise ValidationError("该手机号未注册（请到店建档）")
    children = (
        db.query(Child)
        .filter(Child.parent_id == parent.id, Child.is_deleted == 0)
        .order_by(Child.id)
        .all()
    )
    return {
        "token": _parent_token(parent.id),
        "parent": {"id": parent.id, "name": parent.name, "phone": parent.phone},
        "children": [
            {
                "id": c.id,
                "name": c.name,
                "english_name": c.english_name,
                "member_status": c.member_status,
                "avatar": c.avatar,
            }
            for c in children
        ],
    }
