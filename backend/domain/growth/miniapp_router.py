# backend/domain/growth/miniapp_router.py — 小程序测验与成长 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.database import get_db
from backend.domain.growth.board_service import LeaderboardService, PassportService
from backend.domain.growth.report_service import ReportService
from backend.domain.growth.service import GrowthService, QuizService
from backend.domain.identity import guards
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
    guards.require_member_action(db, child, guards.QUIZ)
    return QuizService(db).get_quiz(child, book_id)


@router.post("/quiz/{book_id}/submit")
def submit_quiz(book_id: int, body: QuizSubmitRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, body.child_id)
    guards.require_member_action(db, child, guards.QUIZ)
    return QuizService(db).submit(child, book_id, body.answers)


@router.get("/growth/summary")
def growth_summary(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.PASSPORT_VIEW)
    return GrowthService(db).summary(child)


@router.get("/points")
def points_ledger(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.POINTS_VIEW)
    return GrowthService(db).points_list(child_id)


@router.get("/leaderboard")
def leaderboard(period: str = "week", child_id: int = 0, auth: Any = Depends(get_current_parent)):
    """五榜单（周期榜仅有效会员可见；总榜含历史学员）。"""
    parent, db = auth
    viewer = _child_of_parent(db, parent.id, child_id)
    if not viewer.is_active_member and period != "total":
        raise ValidationError("入会后可查看排行榜")
    return LeaderboardService(db).board(viewer, period)


@router.get("/passport")
def passport(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.PASSPORT_VIEW)
    return PassportService(db).passport(child)


@router.get("/reports/{kind}")
def report(kind: str, child_id: int, auth: Any = Depends(get_current_parent)):
    """周报/月报数据（家长预览）。R-313：未缴费禁；过期/退会只读。"""
    parent, db = auth
    child = _child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.REPORT_VIEW)
    svc = ReportService(db)
    data = svc.report_data(child, kind)
    data["image_url"] = f"/api/miniapp/reports/{kind}/image?child_id={child_id}"
    return data


@router.get("/reports/{kind}/image")
def report_image(kind: str, child_id: int, token: str = "", db: Session = Depends(get_db)):
    """周报/月报图片（query token：图片组件无法带头）。"""
    from backend.domain.reading.miniapp_router import _parent_from_token

    parent = _parent_from_token(token, db)
    child = (
        db.query(Child)
        .filter(Child.id == child_id, Child.parent_id == parent.id, Child.is_deleted == 0)
        .first()
    )
    if not child:
        raise ValidationError("孩子不存在")
    rel = ReportService(db).generate_image(child, kind)
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    root_dir = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root_dir, rel))
    if not full.startswith(root_dir) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("报告图片不存在")
    return FileResponse(full, media_type="image/png", filename=os.path.basename(rel))
