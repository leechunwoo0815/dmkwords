"""wm12c refund gateway unknown state

Revision ID: f7b9c3d5e8a1
Revises: e6a8b0d2f4c6
Create Date: 2026-10-08 22:00:00.000000

WM12-C（上线前审查 P0-1 整改）：

`refund_requests.gateway_unknown_at` —— 网关调用抛异常/超时时的**未知态**标记时间。
非空 = 上一笔退款在微信侧的结果未知（可能已受理）：禁止换单号重提（会二次出款），
先走退款查单（`POST /api/admin/refund-requests/{id}/query-gateway`）确认后才能继续。
NULL = 状态已知（从未提交过 / 已知失败 / 已受理等）。

为什么不复用 `status=failed`：failed 是"确定没退成功"，可安全重提；未知道与它语义相反，
混在一个字段里正是本次要修的缺陷（异常落 failed → 换新 out_refund_no 重试 → 二次出款）。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7b9c3d5e8a1"
down_revision: str | Sequence[str] | None = "e6a8b0d2f4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "refund_requests",
        sa.Column(
            "gateway_unknown_at",
            sa.DateTime(),
            nullable=True,
            comment="网关结果未知的标记时间",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("refund_requests", "gateway_unknown_at")
