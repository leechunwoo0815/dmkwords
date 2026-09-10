"""wm14b circle snapshot + parent display_name

Revision ID: f8c4e2a6b7d3
Revises: e7b3d1f9a5c2
Create Date: 2026-09-10 23:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8c4e2a6b7d3"
down_revision: str | Sequence[str] | None = "e7b3d1f9a5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # WM14-B：周榜快照（上榜卡/上升卡数据源；口径与周榜同源）
    op.create_table(
        "circle_rank_snapshots",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False, comment="周一（该周起始日）"),
        sa.Column("rank", sa.Integer(), nullable=False, comment="该周名次（词数倒序）"),
        sa.Column("words", sa.Integer(), nullable=False, comment="该周词数"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_circle_rank_snapshot", "circle_rank_snapshots", ["child_id", "week_start"], unique=True
    )
    op.create_index(
        op.f("ix_circle_rank_snapshots_child_id"),
        "circle_rank_snapshots",
        ["child_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_circle_rank_snapshots_week_start"),
        "circle_rank_snapshots",
        ["week_start"],
        unique=False,
    )

    # WM14-B：家长展示称呼（双署名/被赞通知优先取它，空则回退 name）
    op.add_column(
        "parents",
        sa.Column(
            "display_name",
            sa.String(length=64),
            nullable=True,
            comment="展示称呼（如「Tommy妈妈」；空则回退 name）",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("parents", "display_name")
    op.drop_index(op.f("ix_circle_rank_snapshots_week_start"), table_name="circle_rank_snapshots")
    op.drop_index(op.f("ix_circle_rank_snapshots_child_id"), table_name="circle_rank_snapshots")
    op.drop_index("uq_circle_rank_snapshot", table_name="circle_rank_snapshots")
    op.drop_table("circle_rank_snapshots")
