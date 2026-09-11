"""wm15 circle thumb + liker child

Revision ID: a1d7f3c9e5b8
Revises: f8c4e2a6b7d3
Create Date: 2026-09-11 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1d7f3c9e5b8"
down_revision: str | Sequence[str] | None = "f8c4e2a6b7d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # WM15-R1/B1：卡片双规格——缩略图（无字纯图版）独立列，与 image_path 同生命周期
    op.add_column(
        "circle_posts",
        sa.Column(
            "thumb_path",
            sa.String(length=255),
            nullable=True,
            comment="卡片缩略图（无字纯图版，信息流小图用）",
        ),
    )
    # WM15-R6/C3：点赞名义快照——记录点赞那一刻家长选中的孩子（旧数据/未传为 NULL）
    op.add_column(
        "circle_likes",
        sa.Column(
            "liker_child_id",
            sa.Integer(),
            nullable=True,
            comment="点赞方当时选中的孩子（展示名义快照；旧数据/未传为 NULL）",
        ),
    )
    op.create_index(
        op.f("ix_circle_likes_liker_child_id"), "circle_likes", ["liker_child_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_circle_likes_liker_child_id"), table_name="circle_likes")
    op.drop_column("circle_likes", "liker_child_id")
    op.drop_column("circle_posts", "thumb_path")
