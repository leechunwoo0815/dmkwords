"""deposit ledger idempotency key

B1-5（2026-11-26 双审查）：押金赔偿扣款原先只有"押金行锁"防并发，挡不住**同一请求重放**
（双击提交 / 超时重试 / 前端重发）——那会实打实扣两次钱。给资金台账加幂等键：
客户端带同一个 `client_request_id` 重试时不重复扣款，并发同键由唯一索引兜底。

为什么放在 `deposit_ledgers` 而不是 `deposits`：台账行才是"资金事实"（余额回推靠它），
幂等判定天然属于"这一笔是否已经记过账"。MySQL 的唯一索引允许多个 NULL，
所以历史行与不带 key 的调用不受影响。

Revision ID: d5b7c2e8f1a3
Revises: c3d9a1f7b2e4
Create Date: 2026-11-27 01:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b7c2e8f1a3"
down_revision: Union[str, Sequence[str], None] = "c3d9a1f7b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "deposit_ledgers",
        sa.Column(
            "client_request_id",
            sa.String(length=64),
            nullable=True,
            comment="幂等键（赔偿登记等资金动作；客户端生成，重试同键不重复扣款）",
        ),
    )
    op.create_index(
        "ix_deposit_ledger_client_request_id",
        "deposit_ledgers",
        ["client_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_deposit_ledger_client_request_id", table_name="deposit_ledgers")
    op.drop_column("deposit_ledgers", "client_request_id")
