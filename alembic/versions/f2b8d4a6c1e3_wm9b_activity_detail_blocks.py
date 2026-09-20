"""wm9b activity detail_blocks

Revision ID: f2b8d4a6c1e3
Revises: e5c1a7d3b9f4
Create Date: 2026-09-20 18:10:00.000000

活动图文详情（2026-09-20 客户需求）：「活动详情页面除了展示活动简介和费用外，下面还要可以展示
丰富的文字和图片信息，类似公众号一样，吸引人参加」——`activities` 加 `detail_blocks`。

为什么用 JSON 文本列：图块是**整体读写的展示快照**（段落/图片按顺序排列，没有按块查询或统计的需求），
与阅读圈 `circle_posts.card_data` 同款口径；真需要检索时再拆子表不迟。
`description`（简介）保持不动——详情页仍是「简介 + 图文详情」两段，客户口径如此。

downgrade：直接删列。旧端不读该字段，回滚后活动其余行为不变（图文内容丢失属预期）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8d4a6c1e3"
down_revision: str | Sequence[str] | None = "e5c1a7d3b9f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "activities",
        sa.Column("detail_blocks", sa.Text(), nullable=True, comment="图文详情块（JSON 数组）"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("activities", "detail_blocks")
