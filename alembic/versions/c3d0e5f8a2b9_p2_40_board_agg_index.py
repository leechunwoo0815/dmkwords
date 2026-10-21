"""榜单聚合覆盖索引（P2-40，2026-10-20 第九十八批）

背景（第八轮全量审查「性能与容量」族）：榜单/横幅的聚合**随流水线性增长**，且阅读圈
信息流**每个请求都重算**一次全馆周聚合。

修复前计划（12 万条流水造量 + EXPLAIN ANALYZE 实测，60 个孩子）：

```
-> Group aggregate: sum(words_ledgers.word_count)  (actual time=1.74..68.3 rows=60)
    -> Nested loop left join
        -> Filter: (words_ledgers.child_id = children.id)   ← is_deleted=0 只能靠回表过滤
            -> Index lookup on words_ledgers using uq_words_child_book (child_id=...)
```

`uq_words_child_book(child_id, book_id)` 只覆盖 `child_id`，`is_deleted` / `word_count` /
`created_at` 都要**回表**取（每条一次随机读）→ 68.3ms。

本索引 `(child_id, is_deleted, created_at, word_count)` 把 join 键、过滤列、时间窗与求和列
**全部放进索引**：join 条件里的 `is_deleted = 0` 折叠进索引查找，`word_count` 直接由索引求和
（无回表）。修复后同一查询：

```
-> Group aggregate: sum(words_ledgers.word_count)  (actual time=0.448..25.4 rows=60)
        -> Covering index lookup on words_ledgers using ix_words_ledgers_board_agg
             (child_id=children.id, is_deleted=0)
```

实测：聚合 68.3ms → **25.4ms**；端到端 49.2ms → **18.4ms**（同一库同一数据）。

Revision ID: c3d0e5f8a2b9
Revises: b2c9d4e7f1a8
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d0e5f8a2b9"
down_revision: Union[str, Sequence[str], None] = "b2c9d4e7f1a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_words_ledgers_board_agg"
_COLUMNS = ["child_id", "is_deleted", "created_at", "word_count"]


def upgrade() -> None:
    op.create_index(_INDEX_NAME, "words_ledgers", _COLUMNS, unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="words_ledgers")
