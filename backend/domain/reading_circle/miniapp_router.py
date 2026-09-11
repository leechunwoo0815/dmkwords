# backend/domain/reading_circle/miniapp_router.py — 小程序阅读圈 API（/api/miniapp/circle）
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.database import get_db
from backend.domain.identity.auth import _parent_from_token, child_of_parent, get_current_parent
from backend.domain.reading_circle.card_engine import post_card_image
from backend.domain.reading_circle.service import CircleService

router = APIRouter(tags=["circle-miniapp"])


class CircleLikeRequest(BaseSchema):
    #: 点赞方当前选中的孩子（fix33 R2：社交主体=孩子，**必填**）
    child_id: int

    @model_validator(mode="before")
    @classmethod
    def _require_child(cls, data):
        """缺孩子 → 422「请先选择孩子」（老版本端送 `{child_id: null}` 走这里；
        用 PydanticCustomError 而非 ValueError，避免 msg 被加上 "Value error, " 前缀）。"""
        if not isinstance(data, dict) or not data.get("child_id"):
            raise PydanticCustomError("child_required", "请先选择孩子")
        return data


class CircleShareRequest(BaseSchema):
    child_id: int
    card_type: str
    ref_id: int


@router.get("/circle/posts")
def circle_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    child_id: int | None = Query(None, description="观看方当前选中的孩子（liked_by_me 口径）"),
    auth: Any = Depends(get_current_parent),
):
    """信息流（时间倒序真分页 + 置顶帖置首）。"""
    parent, db = auth
    return CircleService(db).list_posts(parent, page, page_size, child_id)


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
def circle_like(
    post_id: int,
    body: CircleLikeRequest | None = None,
    auth: Any = Depends(get_current_parent),
):
    """点赞（fix33 R2：**社交主体=孩子**，child_id 必填；一孩一赞；被赞通知同事务）。

    缺 child_id → 422「请先选择孩子」（连带覆盖「整包 body 都不带」的老端）。
    """
    parent, db = auth
    if body is None:
        raise ValidationError("请先选择孩子")
    child = child_of_parent(db, parent.id, body.child_id)
    return CircleService(db).like(parent, post_id, child)


@router.delete("/circle/posts/{post_id}/like")
def circle_unlike(
    post_id: int,
    child_id: int | None = Query(None, description="点赞主体孩子（定位取消的赞）"),
    auth: Any = Depends(get_current_parent),
):
    """取消点赞（按孩子主体定位行；DELETE 走 query 而非 body——客户端不友好先例）。"""
    parent, db = auth
    if not child_id:
        raise ValidationError("请先选择孩子")
    child = child_of_parent(db, parent.id, child_id)
    return CircleService(db).unlike(post_id, child)


@router.delete("/circle/posts/{post_id}")
def circle_delete_my_post(post_id: int, auth: Any = Depends(get_current_parent)):
    """家长删自己的帖（Q15 裁决：无 body——鉴权=parent token + post.parent_id 归属，
    删除权仅家长与超管；DELETE 带 body 客户端不友好）。"""
    parent, db = auth
    return CircleService(db).delete_post(parent, post_id)


@router.get("/circle/children/{child_id}/profile")
def circle_child_profile(child_id: int, auth: Any = Depends(get_current_parent)):
    """孩子名片页（R4）：英文名/头像/成就数据 + 勋章墙 + TA 的帖子（隐私见 profile_service）。"""
    from backend.domain.reading_circle.profile_service import CircleProfileService

    parent, db = auth
    svc = CircleProfileService(db)
    child = svc.get_child(child_id)
    data = svc.child_profile(child)
    data["posts"] = svc.child_posts(child, parent.id)
    return data


@router.get("/circle/children/{child_id}/poster")
def circle_child_poster(child_id: int, token: str = "", db: Session = Depends(get_db)):
    """名片海报图（R5；query token：image 组件无法带头——照报告图片先例）。"""
    from fastapi.responses import FileResponse

    from backend.common.exceptions import NotFoundError
    from backend.config import get_settings
    from backend.domain.reading_circle.profile_service import CircleProfileService

    _parent_from_token(token, db)
    svc = CircleProfileService(db)
    rel = svc.render_poster(svc.get_child(child_id))
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root) or not os.path.isfile(full):
        raise NotFoundError("名片海报文件不存在")
    return FileResponse(full, media_type="image/png")


@router.get("/circle/posts/{post_id}/thumb")
def circle_post_thumb(post_id: int, token: str = "", db: Session = Depends(get_db)):
    """帖子缩略图（无字纯图版，信息流小图；旧帖回落大图——post_thumb_image 内兜底）。"""
    from fastapi.responses import FileResponse

    from backend.common.exceptions import NotFoundError
    from backend.config import get_settings
    from backend.domain.reading_circle.card_engine import post_thumb_image

    _parent_from_token(token, db)
    rel = post_thumb_image(db, post_id)
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root) or not os.path.isfile(full):
        raise NotFoundError("缩略图文件不存在")
    return FileResponse(full, media_type="image/png")


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
