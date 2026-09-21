"""borrow records returned_by

Revision ID: c7e1f5a9b3d2
Revises: b4d8e2f6a1c5
Create Date: 2026-09-21 12:10:00.000000

借阅台 D 批（2026-09-21 任务包）：`borrow_records` 加 `returned_by`
——用户拍板"要归还操作人"。此前只记 `borrowed_by`，还书是"谁办的"查不到；
借还记录查询（`GET /api/admin/circulation/records`）要能追到人。

**不做数据回填**：历史还书记录当时确实没记操作人，凭空编一个等于伪造审计——
列表里如实显示"未记录"（前端文案）。新增行由 `return_book()` 写入。

downgrade：删列（回滚后借还记录仍可查，只是不显示归还操作人）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e1f5a9b3d2"
down_revision: str | Sequence[str] | None = "b4d8e2f6a1c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "borrow_records",
        sa.Column(
            "returned_by",
            sa.Integer(),
            nullable=True,
            comment="办理归还的馆员ID（历史行 NULL=未记录）",
        ),
    )


def downgrade() -> None:
    op.drop_column("borrow_records", "returned_by")
