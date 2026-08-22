"""wm9 activity tables

Revision ID: c9d3a6b8e2f1
Revises: b8e2f5a7c1d9
Create Date: 2026-08-22 23:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d3a6b8e2f1"
down_revision: Union[str, Sequence[str], None] = "b8e2f5a7c1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "activities",
        sa.Column("title", sa.String(length=120), nullable=False, comment="活动名称"),
        sa.Column("activity_type", sa.String(length=30), nullable=False, comment="类型"),
        sa.Column("start_at", sa.DateTime(), nullable=False, comment="开始时间"),
        sa.Column("location", sa.String(length=200), nullable=False, comment="地点"),
        sa.Column("max_quota", sa.Integer(), nullable=False, comment="最大报名人数"),
        sa.Column("fee", sa.Numeric(10, 2), nullable=False, comment="报名费用（0=免费）"),
        sa.Column("description", sa.Text(), nullable=True, comment="活动介绍"),
        sa.Column("cover_path", sa.String(length=255), nullable=True, comment="封面图"),
        sa.Column("member_only", sa.SmallInteger(), nullable=False, comment="1=仅会员"),
        sa.Column("enroll_deadline", sa.DateTime(), nullable=True, comment="报名截止"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activities_status"), "activities", ["status"], unique=False)

    op.create_table(
        "activity_enrollments",
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True, comment="收费活动关联订单"),
        sa.Column(
            "ticket_code", sa.String(length=32), nullable=False, comment="入场券码（签到用）"
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("checked_in_at", sa.DateTime(), nullable=True, comment="签到时间"),
        sa.Column("checked_in_by", sa.Integer(), nullable=True, comment="签到操作管理员"),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_code"),
    )
    op.create_index(
        "uq_enroll_active_child", "activity_enrollments", ["activity_id", "child_id"], unique=False
    )
    op.create_index(
        op.f("ix_activity_enrollments_activity_id"),
        "activity_enrollments",
        ["activity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_activity_enrollments_child_id"), "activity_enrollments", ["child_id"], unique=False
    )
    op.create_index(
        op.f("ix_activity_enrollments_status"), "activity_enrollments", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_activity_enrollments_status"), table_name="activity_enrollments")
    op.drop_index(op.f("ix_activity_enrollments_child_id"), table_name="activity_enrollments")
    op.drop_index(op.f("ix_activity_enrollments_activity_id"), table_name="activity_enrollments")
    op.drop_index("uq_enroll_active_child", table_name="activity_enrollments")
    op.drop_table("activity_enrollments")
    op.drop_index(op.f("ix_activities_status"), table_name="activities")
    op.drop_table("activities")
