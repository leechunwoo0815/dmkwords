# backend/domain/growth/miniapp_router.py — 小程序测验与成长 API（/api/miniapp）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.common.exceptions import ValidationError
from backend.common.file_utils import image_media_type
from backend.common.list_meta import stamp_list_meta
from backend.database import get_db
from backend.domain.growth.board_service import LeaderboardService, PassportService
from backend.domain.growth.passed_books_service import PassedBooksService
from backend.domain.growth.report_service import ReportService
from backend.domain.growth.service import GrowthService, QuizService
from backend.domain.identity import guards
from backend.domain.identity.auth import _parent_from_token, child_of_parent, get_current_parent

router = APIRouter(tags=["growth-miniapp"])


class QuizSubmitRequest(BaseSchema):
    child_id: int
    answers: list[str] = Field(..., min_length=1)


@router.get("/quiz/status-batch")
def quiz_status_batch(
    child_id: int,
    book_ids: str,
    auth: Any = Depends(get_current_parent),
):
    """T43（U2）：书架角标批量状态（3 次 IN 查询禁 N+1；book_ids 逗号分隔≤50）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    ids = [int(x) for x in book_ids.split(",") if x.strip().isdigit()]
    from backend.domain.growth.quiz_batch import quiz_status_batch

    return {"items": quiz_status_batch(db, child, ids)}


@router.get("/quiz/{book_id}")
def get_quiz(book_id: int, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.QUIZ)
    return QuizService(db).get_quiz(child, book_id)


@router.post("/quiz/{book_id}/submit")
def submit_quiz(book_id: int, body: QuizSubmitRequest, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, body.child_id)
    guards.require_member_action(db, child, guards.QUIZ)
    return QuizService(db).submit(child, book_id, body.answers)


@router.get("/growth/summary")
def growth_summary(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.PASSPORT_VIEW)
    return GrowthService(db).summary(child)


@router.get("/points")
def points_ledger(response: Response, child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.POINTS_VIEW)
    rows = GrowthService(db).points_list(child_id)
    # P2-44（第九轮 P3 补齐）：本页硬上限 100 经响应头如实告知——原先静默截断，
    # 家长看到"就这些了"，实际更早的流水被无声吞掉。
    stamp_list_meta(response, loaded=len(rows), limit=GrowthService.POINTS_LIST_LIMIT)
    return rows


@router.get("/growth/passed-books")
def passed_books(response: Response, child_id: int, auth: Any = Depends(get_current_parent)):
    """已通过测验的书（书架「已通过」页签：封面 + 词数 + 最佳成绩）。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.PASSPORT_VIEW)
    rows = PassedBooksService(db).list_for(child_id)
    # P2-44（第九轮 P3 补齐）：原先完全无上限，现已按"最近通过"取前 200 条——上限如实告知。
    stamp_list_meta(response, loaded=len(rows), limit=PassedBooksService.LIST_LIMIT)
    return rows


@router.get("/leaderboard")
def leaderboard(period: str = "week", child_id: int = 0, auth: Any = Depends(get_current_parent)):
    """五榜单（周期榜仅有效会员可见；总榜含历史学员）。

    W2-D8（第十轮 P3，2026-10-25）：`child_id` 缺省时给出**参数语义**的报错。原先把默认值 0
    直接喂给归属校验 → "孩子不存在"（422），家长/客服据此以为"孩子数据没了"，
    实际只是少传了一个参数（排行榜的榜单口径与查看者身份有关，必须知道是哪个孩子）。
    """
    parent, db = auth
    if not child_id:
        raise ValidationError("缺少 child_id 参数（排行榜需要知道查看者是哪个孩子）")
    viewer = child_of_parent(db, parent.id, child_id)
    if not viewer.is_active_member and period != "total":
        raise ValidationError("入会后可查看排行榜")
    return LeaderboardService(db).board(viewer, period)


@router.get("/passport")
def passport(child_id: int, auth: Any = Depends(get_current_parent)):
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.PASSPORT_VIEW)
    return PassportService(db).passport(child)


@router.get("/reports/{kind}")
def report(kind: str, child_id: int, auth: Any = Depends(get_current_parent)):
    """周报/月报数据（家长预览）。R-313：未缴费禁；过期/退会只读。"""
    parent, db = auth
    child = child_of_parent(db, parent.id, child_id)
    guards.require_member_action(db, child, guards.REPORT_VIEW)
    svc = ReportService(db)
    data = svc.report_data(child, kind)
    data["image_url"] = f"/api/miniapp/reports/{kind}/image?child_id={child_id}"
    return data


@router.get("/reports/{kind}/image")
def report_image(kind: str, child_id: int, token: str = "", db: Session = Depends(get_db)):
    """周报/月报图片（query token：图片组件无法带头）。"""
    parent = _parent_from_token(token, db)
    child = child_of_parent(db, parent.id, child_id)
    rel = ReportService(db).generate_image(child, kind)
    import os

    from fastapi.responses import FileResponse

    from backend.config import get_settings

    root_dir = os.path.abspath(get_settings().UPLOADS_DIR)
    full = os.path.abspath(os.path.join(root_dir, rel))
    # P2-27（2026-10-17 第六十九批·大审查）：此前用**裸前缀** `full.startswith(root_dir)`——
    # 同前缀的兄弟目录（`uploads-evil/x.jpg`）能骗过它。同类已在 catalog/reading 两处收口到
    # `media_paths.is_within`，这里是全仓最后一处漏改（口径漏改，当前不可利用）。
    from backend.common.media_paths import is_within

    if not is_within(root_dir, full) or not os.path.isfile(full):
        from backend.common.exceptions import NotFoundError

        raise NotFoundError("报告图片不存在")
    return FileResponse(full, media_type=image_media_type(rel), filename=os.path.basename(rel))
