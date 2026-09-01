"""wm3-b2: orders.voucher_path（收款凭证图路径）

WM3-B2 确认收款凭证上传：待人工确认订单可传凭证图（voucher/ 目录，统一 JPG），
已支付订单可查看。列 comment 与 backend/domain/identity/models.py 保持一致
（alembic check 零差异）。

Revision ID: a3b5c7d9e1f3
Revises: b8d4f2a6c9e1
Create Date: 2026-09-01 19:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3b5c7d9e1f3"
down_revision: Union[str, Sequence[str], None] = "b8d4f2a6c9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "voucher_path",
            sa.String(length=255),
            nullable=True,
            comment="收款凭证图路径（WM3-B2，voucher/ 目录）",
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "voucher_path")
