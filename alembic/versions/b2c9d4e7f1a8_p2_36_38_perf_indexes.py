"""三处性能索引（P2-36/37/38，2026-10-20 第九十七批）

背景（第八轮全量审查「性能与容量」族，EXPLAIN 实测）：三处高频查询**没有任何可用索引**——

| # | 查询 | 修复前计划（6 万行造量实测） |
|---|---|---|
| P2-36 | 仪表盘借出/归还趋势：`WHERE is_deleted=0 AND borrowed_at >= ?` GROUP BY DATE | `type=ALL` 全表扫 **59,899 行** + Using temporary |
| P2-37 | 审计列表：`WHERE is_deleted=0 ORDER BY created_at DESC, id DESC LIMIT 20` | `type=ALL` + **Using filesort** |
| P2-38 | 阅读圈置顶槽：`WHERE is_deleted=0 AND is_pinned=1 ORDER BY created_at DESC` | 走 `ix_circle_posts_is_deleted_created_at` 但 `ref=const` 只用到首列 → **扫 29,932 行**（命中 0 条） |

三张表都是"只增不减"的业务表（借还流水 / 审计 / 帖子），随历史线性变慢；仪表盘 60s 轮询、
审计列表每次翻页、信息流每页都各付一次。

**索引口径**（照 `ix_borrow_records_status_due_at` 既有先例：`is_deleted` 打头 + 实际筛选列）：

  · `borrow_records`：`(is_deleted, borrowed_at)` + `(is_deleted, returned_at)`
  · `audit_logs`：`(is_deleted, created_at)`（InnoDB 二级索引隐含主键后缀 →
    `ORDER BY created_at DESC, id DESC` 可走**反向索引扫描**，消掉 filesort）
  · `circle_posts`：`(is_deleted, is_pinned, created_at)`

Revision ID: b2c9d4e7f1a8
Revises: a1b8c4d9e2f3
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c9d4e7f1a8"
down_revision: Union[str, Sequence[str], None] = "a1b8c4d9e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = (
    ("ix_borrow_records_is_deleted_borrowed_at", "borrow_records", ["is_deleted", "borrowed_at"]),
    ("ix_borrow_records_is_deleted_returned_at", "borrow_records", ["is_deleted", "returned_at"]),
    ("ix_audit_logs_is_deleted_created_at", "audit_logs", ["is_deleted", "created_at"]),
    (
        "ix_circle_posts_is_deleted_pinned_created_at",
        "circle_posts",
        ["is_deleted", "is_pinned", "created_at"],
    ),
)


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, unique=False)


def downgrade() -> None:
    for name, table, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
