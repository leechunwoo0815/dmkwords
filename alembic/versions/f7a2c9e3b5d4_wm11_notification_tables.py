"""wm11 notification center: notifications / task_run_logs / dead_letters

WM11 通知任务看板：
- notifications 站内消息（必达保底）+ 微信订阅尽力送达记录（发送状态/失败原因）
- task_run_logs 定时任务运行记录（管理端任务看板）
- dead_letters 事件死信落库（D6 定正式形态；此前仅结构化日志）

列 comment 与 backend/common/notification_models.py 保持一致（alembic check 零差异）。

Revision ID: f7a2c9e3b5d4
Revises: e1f2a8c4b6d3
Create Date: 2026-08-29 00:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a2c9e3b5d4"
down_revision: Union[str, Sequence[str], None] = "e1f2a8c4b6d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("parent_id", sa.Integer(), nullable=False, comment="接收家长ID"),
        sa.Column("child_id", sa.Integer(), nullable=True, comment="关联孩子ID（可为空）"),
        sa.Column(
            "scene", sa.String(length=64), nullable=False, comment="场景标识（如 borrow.success）"
        ),
        sa.Column(
            "category",
            sa.String(length=24),
            nullable=False,
            server_default="",
            comment="分类（资金/借阅/阅读/…）",
        ),
        sa.Column("title", sa.String(length=100), nullable=False, comment="中文标题"),
        sa.Column(
            "content", sa.String(length=500), nullable=False, server_default="", comment="中文内容"
        ),
        sa.Column(
            "ref_type",
            sa.String(length=32),
            nullable=False,
            server_default="",
            comment="业务对象类型",
        ),
        sa.Column(
            "ref_id", sa.String(length=64), nullable=False, server_default="", comment="业务对象ID"
        ),
        sa.Column(
            "dedup_key",
            sa.String(length=64),
            nullable=False,
            server_default="",
            comment="去重键（提醒节点/固定1）",
        ),
        sa.Column("read_at", sa.DateTime(), nullable=True, comment="家长已读时间"),
        sa.Column(
            "wechat_status",
            sa.String(length=16),
            nullable=False,
            server_default="none",
            comment="微信通道状态",
        ),
        sa.Column(
            "wechat_error",
            sa.String(length=500),
            nullable=False,
            server_default="",
            comment="微信发送失败/跳过原因",
        ),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_id",
            "scene",
            "ref_type",
            "ref_id",
            "dedup_key",
            "is_deleted",
            name="uq_notif_dedup",
        ),
    )
    op.create_index("ix_notifications_parent_id", "notifications", ["parent_id"])
    op.create_index("ix_notifications_child_id", "notifications", ["child_id"])
    op.create_index("ix_notifications_scene", "notifications", ["scene"])

    op.create_table(
        "task_run_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("task_name", sa.String(length=64), nullable=False, comment="任务标识"),
        sa.Column("started_at", sa.DateTime(), nullable=False, comment="开始时间"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="结束时间"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
            comment="结果状态",
        ),
        sa.Column(
            "processed", sa.Integer(), nullable=False, server_default="0", comment="处理条数"
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_run_logs_task_name", "task_run_logs", ["task_name"])

    op.create_table(
        "dead_letters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("event_type", sa.String(length=64), nullable=False, comment="事件类型"),
        sa.Column("handler_name", sa.String(length=100), nullable=False, comment="处理器名"),
        sa.Column("payload", sa.Text(), nullable=True, comment="事件负载JSON"),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default="0", comment="重试次数"
        ),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dead_letters_event_type", "dead_letters", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_dead_letters_event_type", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_index("ix_task_run_logs_task_name", table_name="task_run_logs")
    op.drop_table("task_run_logs")
    op.drop_index("ix_notifications_scene", table_name="notifications")
    op.drop_index("ix_notifications_child_id", table_name="notifications")
    op.drop_index("ix_notifications_parent_id", table_name="notifications")
    op.drop_table("notifications")
