"""wm14 circle tables

Revision ID: e7b3d1f9a5c2
Revises: 10d43873633c
Create Date: 2026-09-10 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7b3d1f9a5c2"
down_revision: Union[str, Sequence[str], None] = "10d43873633c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "circle_posts",
        sa.Column("parent_id", sa.Integer(), nullable=False, comment="发布家长ID"),
        sa.Column("child_id", sa.Integer(), nullable=False, comment="成就孩子ID"),
        sa.Column("card_type", sa.String(length=30), nullable=False, comment="卡片类型"),
        sa.Column(
            "ref_id",
            sa.Integer(),
            nullable=False,
            comment="成就关联ID（按类型语义见常量注释）",
        ),
        sa.Column("card_data", sa.Text(), nullable=False, comment="卡片数据 JSON 快照（冻结）"),
        sa.Column(
            "image_path", sa.String(length=255), nullable=True, comment="卡片图（uploads/circle/）"
        ),
        sa.Column("like_count", sa.Integer(), nullable=False, comment="点赞数（原子更新）"),
        sa.Column("admin_liked", sa.SmallInteger(), nullable=False, comment="1=馆长赞"),
        sa.Column("is_pinned", sa.SmallInteger(), nullable=False, comment="1=置顶（同时最多1条）"),
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
        "ix_circle_post_achievement",
        "circle_posts",
        ["child_id", "card_type", "ref_id"],
        unique=False,
    )
    op.create_index(op.f("ix_circle_posts_parent_id"), "circle_posts", ["parent_id"], unique=False)
    op.create_index(op.f("ix_circle_posts_child_id"), "circle_posts", ["child_id"], unique=False)

    op.create_table(
        "circle_likes",
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_circle_like", "circle_likes", ["post_id", "parent_id"], unique=True)
    op.create_index(op.f("ix_circle_likes_post_id"), "circle_likes", ["post_id"], unique=False)
    op.create_index(op.f("ix_circle_likes_parent_id"), "circle_likes", ["parent_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_circle_likes_parent_id"), table_name="circle_likes")
    op.drop_index(op.f("ix_circle_likes_post_id"), table_name="circle_likes")
    op.drop_index("uq_circle_like", table_name="circle_likes")
    op.drop_table("circle_likes")
    op.drop_index(op.f("ix_circle_posts_child_id"), table_name="circle_posts")
    op.drop_index(op.f("ix_circle_posts_parent_id"), table_name="circle_posts")
    op.drop_index("ix_circle_post_achievement", table_name="circle_posts")
    op.drop_table("circle_posts")
