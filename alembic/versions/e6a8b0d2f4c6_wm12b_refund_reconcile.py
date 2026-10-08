"""wm12b refund gateway + reconciliation

Revision ID: e6a8b0d2f4c6
Revises: d5f7a9c1e3b5
Create Date: 2026-10-08 18:00:00.000000

微信线上支付收尾（WM12-B）：

1. `refund_requests` 三列：线上原路退款要跟微信对账——
   `out_refund_no`（商户退款单号，**微信侧幂等键**，回调只带它回来，靠它反解退款单）、
   `gateway_refund_id`（微信退款单号 refund_id）、`gateway_attempts`（提交次数，重试换新单号）。
2. 新表 `payment_reconciliations`：每日对账报表（本地一致性审计 / 微信账单比对各一条），
   只增不改——差异清单是给人看的证据（口径 docs/09 WM12-B §二.5）。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6a8b0d2f4c6"
down_revision: str | Sequence[str] | None = "d5f7a9c1e3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "refund_requests",
        sa.Column(
            "out_refund_no",
            sa.String(length=64),
            nullable=True,
            comment="商户退款单号（微信幂等键）",
        ),
    )
    op.add_column(
        "refund_requests",
        sa.Column(
            "gateway_refund_id",
            sa.String(length=64),
            nullable=True,
            comment="微信退款单号（refund_id）",
        ),
    )
    op.add_column(
        "refund_requests",
        sa.Column(
            "gateway_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="网关提交次数（重试换新单号）",
        ),
    )

    op.create_table(
        "payment_reconciliations",
        sa.Column(
            "bill_date",
            sa.Date(),
            nullable=False,
            comment="对账日（微信账单日；本地审计=当天）",
        ),
        sa.Column("source", sa.String(length=16), nullable=False, comment="local/wechat"),
        sa.Column("status", sa.String(length=12), nullable=False, comment="ok/diff/skipped/failed"),
        sa.Column(
            "checked_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="核对笔数",
        ),
        sa.Column(
            "diff_count", sa.Integer(), nullable=False, server_default="0", comment="差异笔数"
        ),
        sa.Column(
            "detail", sa.Text(), nullable=True, comment="差异明细 JSON 数组 [{kind,ref,message}]"
        ),
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
            comment="scheduled/manual",
        ),
        sa.Column("actor_id", sa.Integer(), nullable=True, comment="手动触发时的操作人"),
        sa.Column("finished_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_reconciliations_bill_date"),
        "payment_reconciliations",
        ["bill_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_reconciliations_status"),
        "payment_reconciliations",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_payment_reconciliations_status"), table_name="payment_reconciliations")
    op.drop_index(
        op.f("ix_payment_reconciliations_bill_date"), table_name="payment_reconciliations"
    )
    op.drop_table("payment_reconciliations")
    op.drop_column("refund_requests", "gateway_attempts")
    op.drop_column("refund_requests", "gateway_refund_id")
    op.drop_column("refund_requests", "out_refund_no")
