"""W3-09：三列收口为 NOT NULL（is_deleted 补 DB 默认 0）

背景（第十轮 W3-09）：41 张业务表的 `is_deleted/create_time/update_time` 在 DB 层
`nullable=YES` 且 `column_default IS NULL`（默认值只在 ORM 侧）。任何绕过 ORM 的写口
插入的行会得到 `is_deleted=NULL`，而全仓查询谓词是 `is_deleted = 0` → 该行对全站
**永久不可见且不报错**（抽查：演示库 0 行；全仓裸 INSERT 仅 2 处且都显式带三列）。

本迁移：
  1. 先把存量 NULL 回填（`is_deleted=0`；时间列用 `NOW()` 兜底，仅影响历史脏行）；
  2. 41 张表逐列 `MODIFY ... NOT NULL`；`is_deleted` 同时补 `DEFAULT 0`；
  3. `create_time/update_time` **不设 DB 默认**——保持应用侧 `datetime.now()` 统一时钟，
     裸 INSERT 漏列会直接报错（响亮失败），而不是静默写 NULL。

**锁表窗口**：MySQL 8.0 改列可空性需要重建表（COPY/INPLACE+rebuild），每张表持有元数据锁；
单馆数据量小（万级以内），预计每表秒级。线上低峰执行；失败不会留下半截版本号
（alembic 版本表在迁移成功后写入）。

Revision ID: f1b3d5e7a9c2
Revises: e4a6b8c0d2f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1b3d5e7a9c2"
down_revision: str | Sequence[str] | None = "e4a6b8c0d2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 带 TimestampMixin 的 41 张业务表（2026-10-28 从 information_schema 实取）
TABLES = (
    "activities",
    "activity_enrollments",
    "admin_notifications",
    "admin_users",
    "audit_logs",
    "book_copies",
    "books",
    "borrow_records",
    "checkin_streak_awards",
    "checkins",
    "child_growth_states",
    "children",
    "circle_likes",
    "circle_posts",
    "circle_rank_snapshots",
    "dead_letters",
    "deposit_ledgers",
    "deposits",
    "dictionary_words",
    "favorites",
    "media_censuses",
    "media_trash_entries",
    "milestone_awards",
    "notifications",
    "observation_reports",
    "orders",
    "parents",
    "payment_reconciliations",
    "point_ledgers",
    "quiz_attempts",
    "quiz_questions",
    "reading_progress",
    "refund_requests",
    "reservations",
    "sms_codes",
    "system_configs",
    "task_run_logs",
    "transfer_requests",
    "vocabularies",
    "withdrawal_requests",
    "words_ledgers",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"UPDATE `{table}` SET is_deleted = 0 WHERE is_deleted IS NULL")
        op.execute(f"UPDATE `{table}` SET create_time = NOW() WHERE create_time IS NULL")
        op.execute(f"UPDATE `{table}` SET update_time = NOW() WHERE update_time IS NULL")
        # MySQL 的 MODIFY 不带 COMMENT 会**丢列注释**——必须显式带上（否则 alembic check 报漂移）
        op.alter_column(
            table,
            "is_deleted",
            existing_type=sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="软删除标记: 0=正常 1=已删除",
        )
        op.alter_column(
            table,
            "create_time",
            existing_type=sa.DateTime(),
            nullable=False,
            comment="创建时间",
        )
        op.alter_column(
            table,
            "update_time",
            existing_type=sa.DateTime(),
            nullable=False,
            comment="更新时间",
        )


def downgrade() -> None:
    for table in TABLES:
        op.alter_column(
            table,
            "is_deleted",
            existing_type=sa.SmallInteger(),
            nullable=True,
            server_default=None,
            comment="软删除标记: 0=正常 1=已删除",
        )
        op.alter_column(
            table,
            "create_time",
            existing_type=sa.DateTime(),
            nullable=True,
            comment="创建时间",
        )
        op.alter_column(
            table,
            "update_time",
            existing_type=sa.DateTime(),
            nullable=True,
            comment="更新时间",
        )
