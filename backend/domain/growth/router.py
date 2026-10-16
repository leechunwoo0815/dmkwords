# backend/domain/growth/router.py — 管理端成长与测验 API（/api/admin）
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from backend.common.base_schema import BaseSchema
from backend.database import get_db
from backend.domain.growth.report_service import ReportAdminService
from backend.domain.growth.service import GrowthService, QuizService
from backend.middleware.admin_rbac import require_perm, require_super_admin

router = APIRouter(tags=["growth-admin"])


class ResetAttemptsRequest(BaseSchema):
    child_id: int
    book_id: int
    reason: str = Field(..., min_length=2, max_length=200, description="重置原因（必填留痕）")


class AdjustPointsRequest(BaseSchema):
    # 2026-10-12 审查 P2（T11）：积分列是 INT，入参原先无边界 → 溢出 500
    # P2 #19（2026-10-16，第三轮大审查）：**只收正数**——原先 `ge=-1_000_000` 允许负值，
    # 与 PRD §7.2「积分不扣、非负、永久」及本字段注释"正数"矛盾，且能把 points_total 打成负数
    # （榜单/护照到处显示负分）。确需纠正多发积分，属产品口径变更，先走裁定再加口子。
    points: int = Field(..., gt=0, le=1_000_000, description="调整积分（正整数，只加不减）")
    reason: str = Field(..., min_length=2, max_length=200, description="调整原因（必填留痕）")


@router.get("/children/{child_id}/growth")
def child_growth(
    child_id: int,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """孩子成长档案：汇总 + 词数流水 + 积分明细 + 测验状态。"""
    return GrowthService(db).child_growth(child_id)


@router.post("/quiz/attempts/reset")
def reset_attempts(
    body: ResetAttemptsRequest,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """重置测验次数（仅超管；成绩不可代标，只能重测）。"""
    return QuizService(db).reset_attempts(admin, body.child_id, body.book_id, body.reason)


@router.post("/children/{child_id}/points/adjust")
def adjust_points(
    child_id: int,
    body: AdjustPointsRequest,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """积分人工调整（线下奖励；必填原因留痕）。"""
    return GrowthService(db).adjust_points(admin, child_id, body.points, body.reason)


@router.post("/growth/levels/recalc")
def recalc_levels(
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """等级阈值变更后的全量重算（只升不降，幂等）。"""
    return GrowthService(db).recalc_levels(admin)


@router.post("/children/{child_id}/milestones/check")
def check_milestones(
    child_id: int,
    admin: Any = Depends(require_super_admin()),
    db: Session = Depends(get_db),
):
    """里程碑补发核对（节点配置调低后用）。"""
    return {"new_nodes": GrowthService(db).check_milestones_now(admin, child_id)}


@router.post("/children/{child_id}/reports/{kind}/generate")
def generate_report(
    child_id: int,
    kind: str,
    admin: Any = Depends(require_perm("member.manage")),
    db: Session = Depends(get_db),
):
    """生成周报/月报图片（管理端触发，走 uploads 静态下发）。"""
    return ReportAdminService(db).generate_for_admin(admin, child_id, kind)
