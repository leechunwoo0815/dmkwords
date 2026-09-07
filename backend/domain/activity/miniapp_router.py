# backend/domain/activity/miniapp_router.py — 小程序活动 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.activity.models import Activity
from backend.domain.activity.service import ActivityService
from backend.domain.identity.auth import child_of_parent, get_current_parent

router = APIRouter(tags=["activity-miniapp"])


@router.get("/activities")
def list_activities(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return ActivityService(db).list_upcoming(child)


@router.get("/activities/carousel")
def activity_carousel():
    """T45（FEAT-082）：首页轮播位（有封面+PUBLISHED+未开始，≤5 条；公开端点——
    活动信息家长可见，无需 child 上下文）。"""
    from backend.database import get_session

    with get_session() as db:
        return {"items": ActivityService(db).carousel()}


@router.get("/activities/{activity_id}/cover")
def activity_cover(
    activity_id: int,
    token: str = "",
    authorization: str | None = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """T45：活动封面（query token 双通道——照书目 covers 先例；封面公开给家长端）。"""
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    # 优先 query token，其次 Authorization 头（照书目 covers 端点同款鉴权）
    from backend.domain.identity.auth import _parent_from_token

    effective_token = token or (authorization or "").replace("Bearer ", "").strip()
    _parent_from_token(effective_token, db)
    rel = db.query(Activity.cover_path).filter(Activity.id == activity_id).scalar()
    if not rel:
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("封面不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("封面文件不存在")
    return FileResponse(full, media_type="image/jpeg")


@router.get("/activities/{activity_id}")
def activity_detail(activity_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return ActivityService(db).detail(activity_id, child)


class EnrollRequest(BaseSchema):
    child_id: int


class EnrollmentActionRequest(BaseSchema):
    child_id: int


@router.post("/activities/{activity_id}/enroll")
def enroll(activity_id: int, body: EnrollRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).enroll(child, activity_id)


@router.get("/enrollments")
def my_enrollments(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return ActivityService(db).my_enrollments(child)


@router.post("/enrollments/{enrollment_id}/cancel")
def cancel_enrollment(
    enrollment_id: int, body: EnrollmentActionRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).cancel(child, enrollment_id)


@router.post("/enrollments/{enrollment_id}/refund-apply")
def apply_refund(
    enrollment_id: int, body: EnrollmentActionRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).apply_refund(child, enrollment_id)
