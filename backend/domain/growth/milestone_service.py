# backend/domain/growth/milestone_service.py — 里程碑批量补发（R11-c2/G4，2026-10-26）
"""从 `GrowthService.recalc_levels` 抽出：一次取全量已授节点，按孩子分组补发。

原实现逐孩子调 `_check_milestones`（每人一条 `MilestoneAward` 查询 = N+1）；
阈值调低触发全量重算时，孩子数 × 1 条 SQL。抽成模块同时守住 service.py 的 800 行红线。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.growth.models import ChildGrowthState, MilestoneAward


def award_pending_milestones(db: Session, states: list[ChildGrowthState], nodes: list[int]) -> int:
    """按已配置节点批量补发勋章；返回新增条数（幂等：已授节点跳过）。"""
    awarded: dict[int, set[int]] = {}
    for child_id, node in (
        db.query(MilestoneAward.child_id, MilestoneAward.node_words)
        .filter(MilestoneAward.is_deleted == 0)
        .all()
    ):
        awarded.setdefault(child_id, set()).add(node)
    created = 0
    for state in states:
        got = awarded.get(state.child_id, set())
        for node in nodes:
            if node <= state.words_total and node not in got:
                db.add(MilestoneAward(child_id=state.child_id, node_words=node))
                created += 1
    return created
