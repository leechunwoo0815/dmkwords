# backend/domain/reading/router.py — 管理端预约管理 + 孩子阅读档案（WM6）
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.reading.service import ReservationAdminService
from backend.middleware.admin_rbac import require_perm

router = APIRouter(tags=["reading-admin"])


class ReservationItemResponse(BaseSchema):
    id: int
    child_id: int
    child_name: str
    parent_name: str
    parent_phone: str
    book_id: int
    book_title: str
    copy_id: int
    status: str
    created_at: datetime
    expires_at: datetime
    expired: bool


class CheckOutResponse(BaseSchema):
    reservation_id: int
    borrow_record_id: int
    due_at: datetime


class FinishedBookItem(BaseSchema):
    book_id: int
    title: str
    author: str | None
    word_count: int | None
    finished_at: datetime
    reading_minutes: int


class ChildReadingProfileResponse(BaseSchema):
    child_id: int
    child_name: str
    member_status: str
    total_finished: int
    total_reading_minutes: int
    total_checkin_days: int
    current_streak: int
    finished_books: list[FinishedBookItem]


@router.get("/reservations", response_model=list[ReservationItemResponse])
def list_reservations(
    status: str | None = None,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """预约管理列表（默认全部；status=active 看锁定中）。"""
    return ReservationAdminService(db).list_reservations(status)


@router.post("/reservations/{reservation_id}/checkout", response_model=CheckOutResponse)
def checkout_reservation(
    reservation_id: int,
    admin: Any = Depends(require_perm("borrow.operate")),
    db: Session = Depends(get_db),
):
    """核销预约转借阅（到店取书）。"""
    record, res = ReservationAdminService(db).checkout(admin, reservation_id)
    return CheckOutResponse(reservation_id=res.id, borrow_record_id=record.id, due_at=record.due_at)


@router.get("/children/{child_id}/reading", response_model=ChildReadingProfileResponse)
def child_reading_profile(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """孩子档案的阅读数据（WM6 手册步骤 14）。"""
    return ReservationAdminService(db).child_reading_profile(child_id)
