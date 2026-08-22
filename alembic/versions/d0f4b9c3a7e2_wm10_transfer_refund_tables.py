"""wm10 transfer refund withdrawal observation tables

Revision ID: d0f4b9c3a7e2
Revises: c9d3a6b8e2f1
Create Date: 2026-08-23 00:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d0f4b9c3a7e2"
down_revision: Union[str, Sequence[str], None] = "c9d3a6b8e2f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "children",
        sa.Column(
            "operation_locked",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
            comment="操作冻结（转让/退会审核中）",
        ),
    )
    op.create_table(
        "refund_requests",
        sa.Column("kind", sa.String(length=10), nullable=False, comment="order/deposit"),
        sa.Column("order_id", sa.Integer(), nullable=True, comment="关联订单（order 类）"),
        sa.Column("deposit_id", sa.Integer(), nullable=True, comment="关联押金（deposit 类）"),
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False, comment="申请退款金额"),
        sa.Column("reason", sa.String(length=200), nullable=False, comment="家长申请原因"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "review_remark",
            sa.String(length=200),
            nullable=True,
            comment="审核备注（拒绝时给家长看）",
        ),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
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
        op.f("ix_refund_requests_child_id"), "refund_requests", ["child_id"], unique=False
    )
    op.create_index(op.f("ix_refund_requests_status"), "refund_requests", ["status"], unique=False)
    op.create_table(
        "withdrawal_requests",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("review_remark", sa.String(length=200), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
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
        op.f("ix_withdrawal_requests_child_id"), "withdrawal_requests", ["child_id"], unique=False
    )
    op.create_index(
        op.f("ix_withdrawal_requests_status"), "withdrawal_requests", ["status"], unique=False
    )
    op.create_table(
        "transfer_requests",
        sa.Column("source_child_id", sa.Integer(), nullable=False),
        sa.Column("target_child_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False, comment="审核截止（超时 expired）"),
        sa.Column("review_remark", sa.String(length=200), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
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
        op.f("ix_transfer_requests_source_child_id"),
        "transfer_requests",
        ["source_child_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transfer_requests_target_child_id"),
        "transfer_requests",
        ["target_child_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transfer_requests_status"), "transfer_requests", ["status"], unique=False
    )
    op.create_table(
        "observation_reports",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("images", sa.Text(), nullable=False, comment="图片路径 JSON 数组"),
        sa.Column("remark", sa.String(length=500), nullable=True, comment="馆员备注"),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
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
        op.f("ix_observation_reports_child_id"), "observation_reports", ["child_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_observation_reports_child_id"), table_name="observation_reports")
    op.drop_table("observation_reports")
    op.drop_index(op.f("ix_transfer_requests_status"), table_name="transfer_requests")
    op.drop_index(op.f("ix_transfer_requests_target_child_id"), table_name="transfer_requests")
    op.drop_index(op.f("ix_transfer_requests_source_child_id"), table_name="transfer_requests")
    op.drop_table("transfer_requests")
    op.drop_index(op.f("ix_withdrawal_requests_status"), table_name="withdrawal_requests")
    op.drop_index(op.f("ix_withdrawal_requests_child_id"), table_name="withdrawal_requests")
    op.drop_table("withdrawal_requests")
    op.drop_index(op.f("ix_refund_requests_status"), table_name="refund_requests")
    op.drop_index(op.f("ix_refund_requests_child_id"), table_name="refund_requests")
    op.drop_table("refund_requests")
    op.drop_column("children", "operation_locked")
