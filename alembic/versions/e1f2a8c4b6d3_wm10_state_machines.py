"""wm10 state machines: refund 7-state, withdrawal 6-state, order.refund_status

P0 修复（docs/10 外部专家复审报告 WM10-01/02/06）：
- refund_requests 扩展 7 态（pending/approved/processing/refunded/failed/rejected/cancelled）
- withdrawal_requests 扩展 6 态（applying/pending_settle/refunding/completed/rejected/cancelled）+ source 来源列
- orders 增加 refund_status（R-308 退款链路状态）
- children 增加 withdraw_reason（退会原因码）
- 历史数据迁移：withdrawal pending→applying / approved→completed；
  refund approved→refunded（旧实现审核即执行完成）；orders refunded→refund_status=refunded

Revision ID: e1f2a8c4b6d3
Revises: d0f4b9c3a7e2
Create Date: 2026-08-24 00:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a8c4b6d3"
down_revision: Union[str, Sequence[str], None] = "d0f4b9c3a7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "orders",
        sa.Column(
            "refund_status",
            sa.String(length=24),
            nullable=False,
            server_default="",
            comment="退款链路状态（R-308）",
        ),
    )
    op.add_column(
        "children",
        sa.Column(
            "withdraw_reason",
            sa.String(length=32),
            nullable=True,
            comment="退会原因码（user_withdrawal/user_refund/membership_transfer）",
        ),
    )
    op.add_column(
        "withdrawal_requests",
        sa.Column(
            "source",
            sa.String(length=24),
            nullable=False,
            server_default="normal",
            comment="来源（normal/refund_linked/transfer_linked）",
        ),
    )
    op.add_column(
        "refund_requests",
        sa.Column(
            "withdrawal_id",
            sa.Integer(),
            nullable=True,
            comment="关联退会申请（退会/退款联动结算生成）",
        ),
    )
    # 历史状态值迁移（旧实现：审核通过即终态）
    op.execute("UPDATE withdrawal_requests SET status = 'applying' WHERE status = 'pending'")
    op.execute("UPDATE withdrawal_requests SET status = 'completed' WHERE status = 'approved'")
    op.execute("UPDATE refund_requests SET status = 'refunded' WHERE status = 'approved'")
    op.execute("UPDATE orders SET refund_status = 'refunded' WHERE status = 'refunded'")


def downgrade() -> None:
    """Downgrade schema."""
    # 状态回写（新态 → 旧最近似值）
    op.execute(
        "UPDATE withdrawal_requests SET status = 'pending' WHERE status IN "
        "('applying', 'pending_settle', 'refunding')"
    )
    op.execute("UPDATE withdrawal_requests SET status = 'approved' WHERE status = 'completed'")
    op.execute(
        "UPDATE refund_requests SET status = 'approved' WHERE status IN ('processing', 'refunded')"
    )
    op.execute("UPDATE refund_requests SET status = 'pending' WHERE status = 'failed'")
    op.drop_column("refund_requests", "withdrawal_id")
    op.drop_column("withdrawal_requests", "source")
    op.drop_column("children", "withdraw_reason")
    op.drop_column("orders", "refund_status")
