"""通知去重预取索引补口（第十轮 W3-08，2026-10-27 B5 批）

背景（第十轮全量审查 W3 族，60k 行造量 EXPLAIN 复核）：

| # | 查询 | 修复前 | 修复后 |
|---|---|---|---|
| W3-08 | 通知去重预取：`scene=? AND ref_type=? AND ref_id IN (...) AND is_deleted=0` | 只命中 `ix_notifications_scene(scene)` → **18,516 行**再逐行过滤 | 新增 `(scene, ref_type, ref_id, is_deleted, parent_id, dedup_key)` → **range 125 行 + Using index（覆盖，无回表）** |

**索引口径**：与 `uq_notif_dedup(parent_id, scene, ref_type, ref_id, dedup_key, is_deleted)`
前缀不同，不冗余；尾列覆盖预取 SELECT 的三列（ref_id / dedup_key / parent_id），
`is_deleted=0` 也下推到索引。

**同批否掉的候选（W3-06，避免后人"顺手优化"回头加）**：`books(is_deleted, id)`
**不加**。60k 造量实测：刚批量插入、统计未刷新时「全部」页确实 `ref 30000 + filesort`；
但 `ANALYZE TABLE books` 后同一语句变成 PRIMARY 逆序扫描 40 行、零 filesort——
**有没有这条索引都一样，优化器从没选中它**。真实处置是批量导入后刷新统计
（InnoDB 自动采样之外的手工兜底），不是加索引。详见 `docs/09` 第一百三十五批。

**锁表窗口（W3-12 同族要求）**：MySQL 8.0 建二级索引走 ONLINE DDL（INPLACE，
允许并发读写），不重建表；仍需短暂元数据锁（MDL）等待在途事务。本迁移只加索引、
不改列，线上低峰执行即可。失败不会留下半截版本号（版本表在 DDL 完成后才写），
重跑前先确认索引是否已存在。

Revision ID: e4a6b8c0d2f4
Revises: 7d1d1adbbd10
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a6b8c0d2f4"
down_revision: str | Sequence[str] | None = "7d1d1adbbd10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    (
        "ix_notifications_scene_ref",
        "notifications",
        ["scene", "ref_type", "ref_id", "is_deleted", "parent_id", "dedup_key"],
    ),
)


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, unique=False)


def downgrade() -> None:
    for name, table, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
