"""wm8b vocab lookup_count

Revision ID: e5c1a7d3b9f4
Revises: c3a9e5b1d7f2
Create Date: 2026-09-15 17:50:00.000000

生词本「查词次数」（2026-09-15，用户点名样板）：
`vocabularies` 加 `lookup_count`，重复查同一个词自增。

为什么必须落库：此前只有 `created_at` 一个时间点，前端无从呈现「这个词我查了 3 次」，
生词本因此没有任何积累感/成就感可言（用户原话：「肯定要有统计的次数」）。
`server_default="1"`：存量行按「至少查过一次」回填，避免历史行为 NULL 被前端当 0。

downgrade：直接删列。`lookup_count` 是新增的展示维度，历史值无从还原也无需还原
（旧端不读该字段，回滚后生词本其余行为不变）。
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e5c1a7d3b9f4"
down_revision: Union[str, Sequence[str], None] = "c3a9e5b1d7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "vocabularies",
        sa.Column(
            "lookup_count",
            sa.Integer(),
            server_default="1",
            nullable=False,
            comment="累计被查次数",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("vocabularies", "lookup_count")
