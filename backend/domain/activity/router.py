# backend/domain/activity/router.py — 管理端活动 API（/api/admin）
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.activity.admin_service import AdminActivityService
from backend.domain.activity.service import ActivityService
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["activity-admin"])


class ActivityUpdateRequest(BaseSchema):
    """T45（FEAT-082·Q8 批复）：编辑白名单 9 字段——activity_type 禁改
    （BaseSchema extra=forbid：传入即 422 显式拒绝）。"""

    title: str | None = Field(None, min_length=1, max_length=120)
    start_at: datetime | None = None
    location: str | None = Field(None, max_length=200)
    max_quota: int | None = Field(None, gt=0, le=1000)
    fee: Decimal | None = Field(None, ge=0)
    description: str | None = Field(None, max_length=2000)
    member_only: bool | None = None
    enroll_deadline: datetime | None = None

    @field_validator("start_at", "enroll_deadline", mode="after")
    @classmethod
    def _strip_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            return v.astimezone().replace(tzinfo=None)
        return v


class ActivityCreateRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=120)
    activity_type: str = Field("book_club")
    start_at: datetime
    location: str = Field("", max_length=200)
    max_quota: int = Field(..., gt=0, le=1000)
    # B-8/T15：金额严禁 float（宪法红线），Decimal 落 Numeric(10,2)
    fee: Decimal = Field(Decimal("0"), ge=0)
    description: str | None = Field(None, max_length=2000)
    member_only: bool = False
    enroll_deadline: datetime | None = None

    @field_validator("start_at", "enroll_deadline", mode="after")
    @classmethod
    def _strip_tz(cls, v: datetime | None) -> datetime | None:
        """T20a：前端 toISOString 发 aware 时间，服务端全 naive——统一剥时区
        （astimezone 转本地时区后剥 tzinfo；本地时区语义，docker-compose TZ=Asia/Shanghai）。"""
        if v is not None and v.tzinfo is not None:
            return v.astimezone().replace(tzinfo=None)
        return v


class SigninRequest(BaseSchema):
    ticket_code: str = Field(..., min_length=4, max_length=32)


class RefundReviewRequest(BaseSchema):
    approve: bool
    remark: str = Field("", max_length=200)


@router.get("/activities")
def list_activities(
    status: str | None = None,
    keyword: str | None = Query(None, max_length=50),
    activity_type: str | None = None,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ActivityService(db).list_admin(status, keyword, activity_type)


@router.get("/activities/{activity_id}")
def get_activity_detail(
    activity_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """T45：活动详情（含报名统计）。"""
    return AdminActivityService(db).get_detail(activity_id)


@router.put("/activities/{activity_id}")
def update_activity(
    activity_id: int,
    body: ActivityUpdateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """T45：活动编辑（仅 PUBLISHED 且未开始；Q7 名额下限/Q8 白名单）。"""
    a = AdminActivityService(db).update(admin, activity_id, body)
    return {"id": a.id, "title": a.title, "status": a.status}


@router.post("/activities/{activity_id}/cover")
async def upload_activity_cover(
    activity_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
):
    """T45：封面上传（R-316 同款通道统一转 JPG；Router 零异常处理纪律）。"""
    data = await file.read()
    a = AdminActivityService(db).upload_cover(admin, activity_id, data, file.filename or "")
    return {"id": a.id, "cover_path": a.cover_path}


@router.get("/activities/{activity_id}/cover-media")
def activity_cover_media(
    activity_id: int,
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
):
    """T45：封面查看（管理端 <img> 用；query token 双通道——照书目 cover-media 先例）。"""
    from fastapi.responses import FileResponse

    from backend.config import get_settings
    from backend.domain.catalog.media_auth import authorize_media

    authorize_media(request, token, db)
    rel = AdminActivityService(db).get_cover_path(activity_id)
    if not rel:
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("封面不存在")
    root = get_settings().UPLOADS_DIR
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(os.path.abspath(root)):
        raise NotFoundError("封面文件不存在")
    return FileResponse(full, media_type="image/jpeg")


@router.post("/activities")
def create_activity(
    body: ActivityCreateRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    a = ActivityService(db).create(admin, body)
    return {"id": a.id, "title": a.title, "status": a.status}


@router.post("/activities/{activity_id}/cancel")
def cancel_activity(
    activity_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """取消整场活动（已付未签到批量转退款待审）。"""
    return ActivityService(db).cancel_activity(admin, activity_id)


@router.get("/activities/{activity_id}/enrollments")
def list_enrollments(
    activity_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ActivityService(db).list_enrollments(activity_id)


@router.post("/activity-signin")
def signin(
    body: SigninRequest,
    admin: Any = Depends(
        require_perm("borrow.operate")
    ),  # T20b（Q1 裁 A）：签到=馆员现场操作（PRD §9.2 语义对齐；原 member.manage，staff 两权皆有，行为零变化）
    db: Session = Depends(get_db),
):
    """扫入场券签到（记录时间 + 操作人）。"""
    return ActivityService(db).signin(admin, body.ticket_code)


@router.get("/activity-refunds")
# S3（20260907）deprecated：T16 后活动退款走统一台账（refund-requests 链），
# 退款中心为唯一审核入口——本端点已无前端调用（死代码留契约兼容，勿新增调用）
def list_refund_pending(
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款待审列表（活动）。"""
    return ActivityService(db).list_refund_pending()


@router.post("/activity-refunds/{enrollment_id}/review")
# S3 deprecated：同上——审核唯一入口=退款中心统一链（本端点委托 RefundService.review 语义不变，测试造数仍可用）
def review_refund(
    enrollment_id: int,
    body: RefundReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款逐单审核（仅超管）。"""
    return ActivityService(db).review_refund(admin, enrollment_id, body.approve, body.remark)
