"""reading unique indexes (P1-8: checkins / reading_progress 唯一索引)

Revision ID: b9d1e3f5a7c2
Revises: a8c0e2f4b6d9
Create Date: 2026-10-09 13:20:00.000000

来源：上线前审查 P1-8（外部专家独立复核 + 本地亲验）：

| 表 | 索引 | 实测缺口 |
|---|---|---|
| checkins | `(child_id, checkin_date, is_deleted)` 唯一 | 6 线程并发完播 → **6 行同日打卡**（原实现 check-then-insert） |
| reading_progress | `(child_id, book_id, is_deleted)` 唯一 | 并发心跳重试 → 同一 (child, book) 两行，进度被 `first()` 随机劈开 → 覆盖率到不了 95%、测验永久解不开 |

应用层配合：两处写点改为**原子 upsert**（`INSERT ... ON DUPLICATE KEY UPDATE`，见
`backend/domain/reading/service.py`）——索引是并发下的最终防线，应用层保证语义。

为什么带 `is_deleted`：与既有 `uq_order_transaction` 同款（软删行不占用唯一键）。

⚠️ 加索引前先查重复：本迁移**不自动删数据**（业务主数据禁止物理删除）——发现重复直接报错
并列出分组样本，由人工按业务口径合并后再跑（当前 dev 库实测 0 重复组）。
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "b9d1e3f5a7c2"
down_revision: str | None = "a8c0e2f4b6d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assert_no_duplicates(table: str, cols: str) -> None:
    """加唯一索引前的防呆：有重复就报错（不静默删），把处理权交给人。"""
    rows = (
        op.get_bind()
        .execute(
            text(
                f"SELECT {cols}, COUNT(*) AS c FROM {table} WHERE is_deleted = 0 "
                f"GROUP BY {cols} HAVING c > 1 LIMIT 20"
            )
        )
        .fetchall()
    )
    if rows:
        raise RuntimeError(
            f"{table} 存在重复分组（{cols}）：{rows[:5]} —— 请先人工合并（同组保留一行，"
            "其余按业务口径处理）再跑本迁移；本迁移不自动删业务数据。"
        )


def upgrade() -> None:
    _assert_no_duplicates("reading_progress", "child_id, book_id")
    _assert_no_duplicates("checkins", "child_id, checkin_date")
    op.create_index(
        "uq_reading_progress_child_book",
        "reading_progress",
        ["child_id", "book_id", "is_deleted"],
        unique=True,
    )
    op.create_index(
        "uq_checkins_child_date",
        "checkins",
        ["child_id", "checkin_date", "is_deleted"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_checkins_child_date", table_name="checkins")
    op.drop_index("uq_reading_progress_child_book", table_name="reading_progress")
