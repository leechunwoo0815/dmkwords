# backend/domain/reading_circle/miniapp_router.py — 小程序阅读圈 API（/api/miniapp/circle）
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.identity.auth import _parent_from_token, child_of_parent, get_current_parent
from backend.domain.reading_circle.card_engine import post_card_image
from backend.domain.reading_circle.service import CircleService

router = APIRouter(tags=["circle-miniapp"])


class CircleShareRequest(BaseSchema):
    child_id: int
    card_type: str
    ref_id: int


@router.get("/circle/posts")
def circle_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    auth: Any = Depends(get_current_parent),
):
    """信息流（时间倒序真分页 + 置顶帖置首）。"""
    parent, db = auth
    return CircleService(db).list_posts(parent, page, page_size)


@router.get("/circle/my-cards")
def circle_my_cards(child_id: int, auth: Any = Depends(get_current_parent)):
    """可晒成就库（已达成未晒 + 已晒分组——历史成就补晒）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return CircleService(db).my_cards(child)


@router.post("/circle/posts")
def circle_share(body: CircleShareRequest, auth: Any = Depends(get_current_parent)):
    """晒卡（服务端校验成就归属 + 同成就终身唯一 + 每日限晒）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return CircleService(db).create_post(parent, child, body.card_type, body.ref_id)


@router.post("/circle/posts/{post_id}/like")
def circle_like(post_id: int, auth: Any = Depends(get_current_parent)):
    """点赞（一心一赞；like_count 原子更新；被赞通知同事务）。"""
    parent, db = auth
    return CircleService(db).like(parent, post_id)


@router.delete("/circle/posts/{post_id}/like")
def circle_unlike(post_id: int, auth: Any = Depends(get_current_parent)):
    """取消点赞。"""
    parent, db = auth
    return CircleService(db).unlike(parent, post_id)


@router.delete("/circle/posts/{post_id}")
def circle_delete_my_post(post_id: int, auth: Any = Depends(get_current_parent)):
    """家长删自己的帖（Q15 裁决：无 body——鉴权=parent token + post.parent_id 归属，
    删除权仅家长与超管；DELETE 带 body 客户端不友好）。"""
    parent, db = auth
    return CircleService(db).delete_post(parent, post_id)


@router.get("/circle/posts/{post_id}/image")
def circle_post_image(post_id: int, token: str = "", db: Session = Depends(get_db)):
    """帖子卡片图（query token：image 组件无法带头——照报告图片先例）。"""
    from fastapi.responses import FileResponse

    from backend.common.exceptions import NotFoundError
    from backend.config import get_settings

    _parent_from_token(token, db)
    rel = post_card_image(db, post_id)
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root) or not os.path.isfile(full):
        raise NotFoundError("卡片图文件不存在")
    return FileResponse(full, media_type="image/png")
