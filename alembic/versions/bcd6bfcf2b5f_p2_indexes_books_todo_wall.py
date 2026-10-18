"""books / admin_notifications 生产规模索引（P2-4 + P2-2，2026-10-18 第七十六批）

背景（第七轮全量审查，两条均 `实测✓`）：

1. **P2-4 书城/书目列表**：`WHERE is_deleted=0 AND status=? ORDER BY id DESC LIMIT 20`
   （书城与管理端列表的默认排序都是 `id DESC`）——`books` 原先只有 PRIMARY/internal_code/isbn，
   造量 3000 行实测退化为 `type=index(key=PRIMARY) + Using where` 的**扫描+过滤**；
   书目上架率低时（演示库 35 本里多数下架）要扫到匹配行为止。

2. **P2-2 待办墙**：`WHERE is_deleted=0 ORDER BY created_at DESC, id DESC`
   ——造量 3000 行实测 `type=ALL, key=NULL, Using where; Using filesort`（**全表扫 + 排序**），
   而徽标计数与列表同源，馆员每次打开待办页都付这个代价。

**索引列尾用 `id` 而不是 `create_time`**（与审查报告的建议略有出入，按实测调整）：
两处列表的真实排序键都是 `id DESC`（id 单调递增 ≈ 入库先后，`create_time` 只用于展示），
若以 `create_time` 收尾，MySQL 仍要 filesort；以 `id` 收尾才能整体走索引顺序。
证据与对照（加索引前后 EXPLAIN）见 `tests/unit/test_audit7_p2_indexes_20261018.py`。

Revision ID: bcd6bfcf2b5f
Revises: a7c1e5d9b3f6
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bcd6bfcf2b5f"
down_revision: Union[str, Sequence[str], None] = "a7c1e5d9b3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """加两条"未删 + 排序键"复合索引（模型侧同定义，见 `Book.__table_args__` /
    `AdminNotification.__table_args__`；`alembic check` 依赖两侧逐字一致）。"""
    op.create_index(
        "ix_admin_notif_deleted_created",
        "admin_notifications",
        ["is_deleted", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_books_deleted_status_id", "books", ["is_deleted", "status", "id"], unique=False
    )


def downgrade() -> None:
    """回退：删两条索引（功能不受影响，只回到"扫描+排序"的老计划）。"""
    op.drop_index("ix_books_deleted_status_id", table_name="books")
    op.drop_index("ix_admin_notif_deleted_created", table_name="admin_notifications")
