"""activity_end_at

Revision ID: 7d1d1adbbd10
Revises: d5f1a83c7042
Create Date: 2026-10-27 13:19:53.532041

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d1d1adbbd10"
down_revision: Union[str, Sequence[str], None] = "d5f1a83c7042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """活动结束时间（docs/23 §二：签到窗 = 开始前 60 分钟 ~ 结束后 30 分钟）。

    历史行按「开始 + 3 小时」回填；列保持可空以兼容旧数据/直接构造，
    服务端创建与签到统一走 `activity_end_at()` 兜底。
    """
    op.add_column(
        "activities",
        sa.Column(
            "end_at",
            sa.DateTime(),
            nullable=True,
            comment="结束时间（签到窗=开始前60分钟~结束后30分钟；历史行为空时按开始+3h）",
        ),
    )
    op.execute(
        "UPDATE activities SET end_at = DATE_ADD(start_at, INTERVAL 3 HOUR) WHERE end_at IS NULL"
    )
    # 产品口径 docs/23 §一：默认仅会员（列默认值仍由模型 default 提供；这里只同步注释）。
    op.alter_column(
        "activities",
        "member_only",
        existing_type=sa.SmallInteger(),
        existing_nullable=False,
        comment="1=仅会员（默认开启；首场/开放日显式关）",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "activities",
        "member_only",
        existing_type=sa.SmallInteger(),
        existing_nullable=False,
        comment="1=仅会员",
    )
    op.drop_column("activities", "end_at")
