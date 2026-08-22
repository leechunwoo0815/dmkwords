# backend/domain/activity/router.py — 管理端活动 API（/api/admin）
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.activity.service import ActivityService
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["activity-admin"])


class ActivityCreateRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=120)
    activity_type: str = Field("book_club")
    start_at: datetime
    location: str = Field("", max_length=200)
    max_quota: int = Field(..., gt=0, le=1000)
    fee: float = Field(0, ge=0)
    description: str | None = Field(None, max_length=2000)
    member_only: bool = False
    enroll_deadline: datetime | None = None


class SigninRequest(BaseSchema):
    ticket_code: str = Field(..., min_length=4, max_length=32)


class RefundReviewRequest(BaseSchema):
    approve: bool
    remark: str = Field("", max_length=200)


@router.get("/activities")
def list_activities(
    status: str | None = None,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    return ActivityService(db).list_admin(status)


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
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """扫入场券签到（记录时间 + 操作人）。"""
    return ActivityService(db).signin(admin, body.ticket_code)


@router.get("/activity-refunds")
def list_refund_pending(
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款待审列表（活动）。"""
    return ActivityService(db).list_refund_pending()


@router.post("/activity-refunds/{enrollment_id}/review")
def review_refund(
    enrollment_id: int,
    body: RefundReviewRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """退款逐单审核（仅超管）。"""
    return ActivityService(db).review_refund(admin, enrollment_id, body.approve, body.remark)
