"""prod query indexes (P1-6: 4 处生产规模索引缺口)

Revision ID: a8c0e2f4b6d9
Revises: f7b9c3d5e8a1
Create Date: 2026-10-08 23:30:00.000000

来源：上线前审查 P1-6（`docs/专家审查-上线前-2026-09-24.md`）——演示量级（<2K 行）全绿，
上量后这四处查询线性变慢。本迁移按**造量 EXPLAIN 实测**（探针库 60k/120k/60k/60k 行，
`gate-runs/2026-10-08/index-probe.log`）落地：

| 表 | 索引 | 查询形状 | 实测（造量 → 实取） |
|---|---|---|---|
| circle_posts | `(is_deleted, created_at)` | 信息流 `WHERE is_deleted=0 ORDER BY created_at DESC, id DESC LIMIT 20` | type=ALL+filesort 15.7ms → ref 逆序索引扫描 0.4ms |
| words_ledgers | `(created_at)` | 周期榜/周报 `WHERE created_at >= ? AND < ? GROUP BY child_id` | 全索引扫描 119802 行 52.2ms → range 2100 行 1.6ms |
| orders | `(create_time)` | 管理端列表 `WHERE is_deleted=0 ORDER BY create_time DESC LIMIT 20` | type=ALL+filesort 57643 行 15.0ms → 逆序索引扫描 20 行 0.3ms |
| borrow_records | `(status, due_at)` | 逾期扫描 `WHERE status=? AND due_at < NOW()` | status 单列取回 29973 行 + filesort 52.0ms → range 19356 行 40.5ms（不再排序） |

两个**实测否掉**的候选（避免后人"顺手优化"回头改）：
  · `orders(is_deleted, create_time)`：估算行数 29880 vs 单列 20，更差——LIMIT 下逆序索引扫描直接停；
  · `borrow_records(is_deleted, status, due_at)`：优化器**根本不选**它（继续走 status 单列 + filesort）。

只加索引、不改列（线上低峰执行）；索引名与模型 `__table_args__` 声明一致（`alembic check` 守门）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c0e2f4b6d9"
down_revision: str | Sequence[str] | None = "f7b9c3d5e8a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (索引名, 表, 列序)——顺序即建索引顺序
INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_circle_posts_is_deleted_created_at", "circle_posts", ("is_deleted", "created_at")),
    ("ix_words_ledgers_created_at", "words_ledgers", ("created_at",)),
    ("ix_orders_create_time", "orders", ("create_time",)),
    ("ix_borrow_records_status_due_at", "borrow_records", ("status", "due_at")),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns), unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
