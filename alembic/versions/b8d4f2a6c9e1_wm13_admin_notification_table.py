"""wm13 admin notification table

WM13 运营审核工作台批次一：
- admin_notifications 管理端待办通知（审核事项级，显示态实时算/审计态事件写）
- 唯一索引 (scene, ref_type, ref_id, dedup_key, is_deleted) 幂等去重（B11）

列 comment 与 backend/common/admin_notification_models.py 保持一致（alembic check 零差异）。

Revision ID: b8d4f2a6c9e1
Revises: f7a2c9e3b5d4
Create Date: 2026-08-31 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8d4f2a6c9e1"
down_revision: Union[str, Sequence[str], None] = "f7a2c9e3b5d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("scene", sa.String(length=64), nullable=False, comment="场景标识（admin.*）"),
        sa.Column("title", sa.String(length=100), nullable=False, comment="事项标题"),
        sa.Column(
            "content", sa.String(length=500), nullable=False, comment="事项内容（含申请原因原文）"
        ),
        sa.Column("ref_type", sa.String(length=32), nullable=False, comment="业务对象类型"),
        sa.Column("ref_id", sa.String(length=64), nullable=False, comment="业务对象ID"),
        sa.Column(
            "applicant_name",
            sa.String(length=128),
            nullable=False,
            comment="申请人（家长名·孩子名/活动名）",
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=True, comment="涉及金额（可空）"),
        sa.Column("dedup_key", sa.String(length=64), nullable=False, comment="去重键（固定1）"),
        sa.Column(
            "handled_at", sa.DateTime(), nullable=True, comment="审计：处理时间（不参与显示态）"
        ),
        sa.Column(
            "handled_by",
            sa.BigInteger(),
            nullable=True,
            comment="审计：处理管理员ID（展示时 JOIN AdminUser 取名）",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="通知创建时间"),
        sa.Column("extra", sa.Text(), nullable=True, comment="扩展JSON（手动处理原因等审计留痕）"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scene",
            "ref_type",
            "ref_id",
            "dedup_key",
            "is_deleted",
            name="uq_admin_notif_dedup",
        ),
    )
    op.create_index("ix_admin_notifications_scene", "admin_notifications", ["scene"])


def downgrade() -> None:
    op.drop_index("ix_admin_notifications_scene", table_name="admin_notifications")
    op.drop_table("admin_notifications")
