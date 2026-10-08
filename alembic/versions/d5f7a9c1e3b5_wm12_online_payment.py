"""wm12 online payment: orders.transaction_id

Revision ID: d5f7a9c1e3b5
Revises: c4e6a8b0d2f4
Create Date: 2026-10-08 13:30:00.000000

微信线上支付（WM12-A）：`orders.transaction_id` = 微信支付单号。

为什么必须落库：回调会重发（微信重试风暴），"同一流水号不得关联两笔订单"这条守卫
只能由 DB 唯一索引兜底——服务层查重只是友好报错（模式手册〇.1 与 P3 ①）。
唯一键含 is_deleted（与既有的 uq_order_no 同款口径）；MySQL 唯一索引允许多行 NULL，
因此未走线上支付的订单（占绝大多数）不受影响。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5f7a9c1e3b5"
down_revision: str | Sequence[str] | None = "c4e6a8b0d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "orders",
        sa.Column(
            "transaction_id",
            sa.String(length=64),
            nullable=True,
            comment="微信支付单号（WM12-A 回调幂等键，唯一索引兜底）",
        ),
    )
    op.create_index("uq_order_transaction", "orders", ["transaction_id", "is_deleted"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_order_transaction", table_name="orders")
    op.drop_column("orders", "transaction_id")
