# backend/domain/activity/miniapp_router.py — 小程序活动 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.activity.admin_service import AdminActivityService
from backend.domain.activity.past_service import PastActivityService
from backend.domain.activity.service import ActivityService
from backend.domain.identity.auth import child_of_parent, get_current_parent

router = APIRouter(tags=["activity-miniapp"])


@router.get("/activities")
def list_activities(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    return ActivityService(db).list_upcoming(child)


@router.get("/activities/past")
def activities_past(
    limit: int = 30,
    offset: int = 0,
    auth: Any = Depends(get_current_parent),
):
    """往期活动回顾（2026-09-20 C 批）——**按时间已过取数**（见 service.list_past 的口径说明）。

    不需要 child_id：往期是**只读回顾**，不涉及报名资格/名额，也就没有 R-313 可见性矩阵的适用面
    （会员专属的**往期**内容对退会家长同样可见——它不产生任何权益）。返回里**不含任何孩子信息**，
    只有活动本身的标题/封面/时间与参与人数聚合。
    """
    parent, db = auth
    return {"items": PastActivityService(db).list_past(limit=limit, offset=offset)}


@router.get("/activities/{activity_id}/detail-image")
def activity_detail_image(
    activity_id: int,
    name: str = "",
    token: str = "",
    authorization: str | None = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    """活动图文配图（2026-09-20）：**只接受 basename**，服务端自行拼 `activity_detail/` 前缀。

    为什么不接受完整相对路径：路径参数化是路径穿越最常见的入口；这里让客户端只能给文件名，
    目录由服务端写死，配合"文件名前缀 = 活动 id"的归属校验，越权与穿越同时封死。
    """
    import os

    from fastapi.responses import FileResponse

    from backend.common.exceptions import NotFoundError
    from backend.common.file_utils import IMAGE_MEDIA_TYPES
    from backend.config import get_settings
    from backend.domain.identity.auth import _parent_from_token

    effective_token = token or (authorization or "").replace("Bearer ", "").strip()
    _parent_from_token(effective_token, db)
    # 只接受 basename（与 URL 侧同名）；名字必须形如「活动id_十六进制.jpg」——
    # 服务端不做任何用户可控的路径拼接，目录写死 + 文件名字符白名单 = 穿越与越权同时封死
    base = name or ""
    stem, ext = os.path.splitext(base)
    ok = (
        base == os.path.basename(base)
        and base.startswith(f"{activity_id}_")
        and ext.lower() in (".jpg", ".jpeg", ".png")
        and len(stem) <= 64
        and all(c.isalnum() or c == "_" for c in stem)
    )
    if not ok:
        raise NotFoundError("配图不存在")
    root = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root, "activity_detail", base))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise NotFoundError("配图文件不存在")
    return FileResponse(full, media_type=IMAGE_MEDIA_TYPES.get(ext.lower(), "image/jpeg"))


@router.get("/activities/carousel")
def activity_carousel():
    """T45（FEAT-082）：首页轮播位（有封面+PUBLISHED+未开始，≤5 条；公开端点——
    活动信息家长可见，无需 child 上下文）。"""
    from backend.database import get_session

    with get_session() as db:
        return {"items": AdminActivityService(db).carousel()}


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
    rel = AdminActivityService(db).get_cover_path(activity_id)
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
