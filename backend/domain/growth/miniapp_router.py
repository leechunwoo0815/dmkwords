# backend/domain/growth/miniapp_router.py — 小程序测验与成长 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.domain.growth.service import GrowthService, QuizService
from backend.domain.identity.models import Child
from backend.domain.reading.miniapp_router import get_current_parent

router = APIRouter(tags=["growth-miniapp"])


def _child_of_parent(db: Session, parent_id: int, child_id: int) -> Child:
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent_id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    return child


class QuizSubmitRequest(BaseSchema):
    child_id: int
    answers: list[str] = Field(..., min_length=1)


@router.get("/quiz/{book_id}")
def get_quiz(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return QuizService(db).get_quiz(child, book_id)


@router.post("/quiz/{book_id}/submit")
def submit_quiz(book_id: int, body: QuizSubmitRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    return QuizService(db).submit(child, book_id, body.answers)


@router.get("/growth/summary")
def growth_summary(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    return GrowthService(db).summary(child)


@router.get("/points")
def points_ledger(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    _child_of_parent(db, parent.id, child_id)
    return GrowthService(db).points_list(child_id)
