# backend/domain/reading_circle/router.py — 管理端阅读圈 API（/api/admin/circle）
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.reading_circle.admin_service import AdminCircleService
from backend.domain.reading_circle.card_engine import CARD_TYPE_LABELS, post_card_image
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["circle-admin"])


class CircleDeleteRequest(BaseSchema):
    """删除必填原因（审计留痕）。"""

    reason: str = ""


@router.get("/circle/posts")
def list_circle_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    card_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    keyword: str | None = Query(None, max_length=50),
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """帖子管理列表（类型下拉「全部」= 不传 card_type；时间段+家长孩子关键词）。"""
    return AdminCircleService(db).list_posts(
        page, page_size, card_type=card_type, start=start, end=end, keyword=keyword
    )


@router.get("/circle/card-types")
def circle_card_types(admin: Any = Depends(require_perm("member.manage"))):
    """类型下拉选项（显式含「全部」——A4 三犯纪律）。"""
    options = [{"value": "", "label": "全部"}]
    options += [{"value": t, "label": label} for t, label in CARD_TYPE_LABELS.items()]
    return {"items": options}


@router.get("/circle/unliked-count")
def circle_unliked_count(
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """未赞徽标数据源（今日新帖 admin_liked=0 计数）。"""
    return {"count": AdminCircleService(db).unliked_count()}


@router.get("/circle/overview")
def circle_overview(
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """运营概览（本周新帖/分享家长数/点赞总数/馆长赞覆盖率/类型分布）。"""
    return AdminCircleService(db).overview()


@router.post("/circle/posts/{post_id}/admin-like")
def circle_admin_like(
    post_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """行内一键馆长赞（特殊文案通知帖主家长）。"""
    return AdminCircleService(db).admin_like(admin, post_id)


@router.delete("/circle/posts/{post_id}/admin-like")
def circle_admin_unlike(
    post_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """取消馆长赞。"""
    return AdminCircleService(db).admin_unlike(admin, post_id)


@router.post("/circle/posts/{post_id}/pin")
def circle_pin(
    post_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """置顶（互斥：同时最多 1 条，置顶新帖自动取消旧置顶帖）。"""
    return AdminCircleService(db).pin(admin, post_id)


@router.delete("/circle/posts/{post_id}/pin")
def circle_unpin(
    post_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """取消置顶。"""
    return AdminCircleService(db).unpin(admin, post_id)


@router.delete("/circle/posts/{post_id}")
def circle_delete_post(
    post_id: int,
    body: CircleDeleteRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """超管删任意帖（必填原因——审计留痕）。"""
    return AdminCircleService(db).delete_post(admin, post_id, body.reason)


@router.get("/circle/posts/{post_id}/image")
def circle_post_image(
    post_id: int,
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
):
    """帖子卡片图（管理端 <img> 用；query token 双通道——照活动 cover-media 先例）。"""
    from fastapi.responses import FileResponse

    from backend.config import get_settings
    from backend.domain.catalog.media_auth import authorize_media

    authorize_media(request, token, db)
    rel = post_card_image(db, post_id)
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("卡片图文件不存在")
    return FileResponse(full, media_type="image/png")
