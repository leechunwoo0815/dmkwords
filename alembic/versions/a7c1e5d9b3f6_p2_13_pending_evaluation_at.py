"""children.pending_evaluation_at —— 待评估时钟落库（P2-13，2026-10-17 第六十九批）

背景（第四轮大审查 P2-13）：`pending_evaluation_weekly` 原先把 `children.update_time` 当
"进入待评估的时刻"，而**任意字段更新**（含家长自助 `PUT /children/{id}/avatar`）都会刷新它
→ "待评估超过 N 天"的时钟被无关更新归零，孩子从每周名单与待办里消失。
判据必须是"进入 pending_evaluation 的时刻"，此前**无落库字段**——本迁移补上：
  · 新增 `pending_evaluation_at`（可空）；
  · 存量数据回填：当前处于 `pending_evaluation` 的孩子用 `update_time` 近似（可得的最好事实）；
  · 应用侧由 `Child.set_member_status` 唯一维护（进入即写、离开即清）。

Revision ID: a7c1e5d9b3f6
Revises: d3e4f5a6b7c8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1e5d9b3f6"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 注释必须与 `identity/models.py:Child.pending_evaluation_at` **逐字一致**
    # （否则 `alembic check` 报 modify_comment；解释写在本文件 docstring，不写进列注释）
    op.add_column(
        "children",
        sa.Column(
            "pending_evaluation_at",
            sa.DateTime(),
            nullable=True,
            comment="进入待评估状态的时刻",
        ),
    )
    # 存量回填：当前在待评估的孩子，用 update_time 作为可得的最好近似
    op.execute(
        "UPDATE children SET pending_evaluation_at = update_time "
        "WHERE member_status = 'pending_evaluation' AND pending_evaluation_at IS NULL"
    )


def downgrade() -> None:
    op.drop_column("children", "pending_evaluation_at")
