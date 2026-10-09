"""parent token generation (P1-11: 家长 token 可撤销)

Revision ID: c2d3e4f5a6b7
Revises: b9d1e3f5a7c2
Create Date: 2026-10-09 15:40:00.000000

来源：上线前审查 P1-11（外部专家复核确认）——家长 token 30 天有效且**不可撤销**
（唯一出路是删家长档案=废号），而它还会出现在媒体 URL（`?token=`）里 → 访问日志/分享
链接泄漏后 30 天内一直可用。

修法：token 载荷带 `gen`（= 本列）；"退出登录"与馆员踢下线 = 本列 +1 → 旧 token 一律 401。
默认 0，历史 token（无 `gen` 字段）按 0 处理 → 未撤销的账号不受影响（向后兼容）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b9d1e3f5a7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "parents",
        sa.Column(
            "token_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="撤销/改绑后 +1，旧 token 失效",
        ),
    )


def downgrade() -> None:
    op.drop_column("parents", "token_generation")
