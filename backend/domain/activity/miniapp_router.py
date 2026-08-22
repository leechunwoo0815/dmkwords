# backend/domain/activity/miniapp_router.py — 小程序活动 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.domain.activity.service import ActivityService
from backend.domain.identity.models import Child
from backend.domain.reading.miniapp_router import get_current_parent

router = APIRouter(tags=["activity-miniapp"])


def _child_of_parent(db: Session, parent_id: int, child_id: int) -> Child:
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


@router.get("/activities")
def list_activities(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return ActivityService(db).list_upcoming(child)


@router.get("/activities/{activity_id}")
def activity_detail(activity_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return ActivityService(db).detail(activity_id, child)


class EnrollRequest(BaseSchema):
    child_id: int


class EnrollmentActionRequest(BaseSchema):
    child_id: int


@router.post("/activities/{activity_id}/enroll")
def enroll(activity_id: int, body: EnrollRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).enroll(child, activity_id)


@router.get("/enrollments")
def my_enrollments(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return ActivityService(db).my_enrollments(child)


@router.post("/enrollments/{enrollment_id}/cancel")
def cancel_enrollment(
    enrollment_id: int, body: EnrollmentActionRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).cancel(child, enrollment_id)


@router.post("/enrollments/{enrollment_id}/refund-apply")
def apply_refund(
    enrollment_id: int, body: EnrollmentActionRequest, auth: Any = Depends(get_current_parent)
):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    return ActivityService(db).apply_refund(child, enrollment_id)
