"""dead_letters 增加"人工销项"字段（P2-1，2026-10-18 第七十八批）

背景（第七轮全量审查 P2-1）：事件处理器重试后仍失败 → 写 `dead_letters` + 日志，**但全系统没有
任何读取面**（`grep DeadLetter backend | grep -v models|events → 0`）——跨域副作用（积分/词数/通知/
审计）失败等于静默数据缺失；同样兜底性质的 `TaskRunLog` 有任务看板，死信连"有多少条待处理"都看不到。

本迁移补两个销项字段，配合新的看板端点（`GET /api/admin/dead-letters` +
`POST /api/admin/dead-letters/{id}/handle`）：
  · `handled_at`：人工处理时刻（看板"标记已处理"写入）；
  · `handled_by`：处理人 `admin_users.id`（留痕，与 `admin_notifications` 同款）。

存量行 `handled_at` 为空 = 未处理（迁移前落库的死信本就是"没人看过"的，语义正确）。

Revision ID: 0f345c121f66
Revises: bcd6bfcf2b5f
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f345c121f66"
down_revision: Union[str, Sequence[str], None] = "bcd6bfcf2b5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 注释必须与 `common/notification_models.py:DeadLetter` 逐字一致（否则 `alembic check` 报 modify_comment）
    op.add_column(
        "dead_letters",
        sa.Column("handled_at", sa.DateTime(), nullable=True, comment="人工处理时刻（看板销项）"),
    )
    op.add_column(
        "dead_letters",
        sa.Column("handled_by", sa.BigInteger(), nullable=True, comment="处理人（admin_users.id）"),
    )


def downgrade() -> None:
    op.drop_column("dead_letters", "handled_by")
    op.drop_column("dead_letters", "handled_at")
