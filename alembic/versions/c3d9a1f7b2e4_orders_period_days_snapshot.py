"""orders period_days snapshot

B1-3（2026-11-26 双审查）：会员费退款的分母原先读**当前配置**（`observation_period_days` /
`formal_period_days`），于是改配置会把历史订单的退款比例一起改掉——"当时谈好的 30 天口径"
在下单之后再改配置就再也还原不了。本迁移给 `orders` 加一列快照，并在建单时冻结。

回填口径（**已知局限，如实记录**）：历史的会员类订单无从得知"下单那一刻的配置值"，
只能按**迁移时刻**的配置尽力补齐（观察期/年费各自取当前值）。若某笔历史单在创建后配置改过，
它的回填值就是错的——但"错一个固定的历史值"仍然好于"跟着配置一直漂"。需要精确复核的
历史单只能人工比对当时的开通记录（`children.member_start/member_expire` 跨度）。

Revision ID: c3d9a1f7b2e4
Revises: 8e18104fc7d9
Create Date: 2026-11-27 00:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d9a1f7b2e4"
down_revision: Union[str, Sequence[str], None] = "8e18104fc7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "period_days",
            sa.Integer(),
            nullable=True,
            comment="会员期限天数快照（下单时冻结；退款分母读它而不是当前配置）",
        ),
    )

    # 回填：只补会员类订单（观察期费 / 年费），值取**迁移时刻**的配置
    conn = op.get_bind()
    config = dict(
        conn.execute(
            sa.text(
                "SELECT config_key, config_value FROM system_configs WHERE config_key IN "
                "('observation_period_days', 'formal_period_days')"
            )
        ).all()
    )
    observation = int(config.get("observation_period_days", 30))
    formal = int(config.get("formal_period_days", 365))
    conn.execute(
        sa.text(
            "UPDATE orders SET period_days = :days "
            "WHERE order_type = 'observation_fee' AND period_days IS NULL"
        ),
        {"days": observation},
    )
    conn.execute(
        sa.text(
            "UPDATE orders SET period_days = :days "
            "WHERE order_type = 'formal_fee' AND period_days IS NULL"
        ),
        {"days": formal},
    )


def downgrade() -> None:
    op.drop_column("orders", "period_days")
