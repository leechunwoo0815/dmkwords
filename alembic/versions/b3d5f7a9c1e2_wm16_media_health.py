"""media health: censuses + trash entries

Revision ID: b3d5f7a9c1e2
Revises: c7e1f5a9b3d2
Create Date: 2026-09-23 21:00:00.000000

媒体体检（docs/15 §二十二，2026-09-23 用户裁定）：孤儿图每日盘点报告 + 回收站条目。
**只新增两张表**，不动任何既有表/列。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3d5f7a9c1e2"
down_revision: str | Sequence[str] | None = "c7e1f5a9b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 每日盘点报告（只读报告；孤儿数字就是运营在任务看板看到的那个数）
    op.create_table(
        "media_censuses",
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            comment="触发来源（scheduled/manual/after_trash/after_restore）",
        ),
        sa.Column(
            "files_total", sa.Integer(), nullable=False, comment="uploads 文件总数（不含回收站）"
        ),
        sa.Column("referenced_total", sa.Integer(), nullable=False, comment="DB 引用路径数"),
        sa.Column("protected_total", sa.Integer(), nullable=False, comment="保护目录跳过数"),
        sa.Column("orphan_files", sa.Integer(), nullable=False, comment="孤儿文件数（可清理）"),
        sa.Column("orphan_bytes", sa.BigInteger(), nullable=False, comment="孤儿字节数"),
        sa.Column("missing_refs", sa.Integer(), nullable=False, comment="引用存在但磁盘缺失数"),
        sa.Column(
            "breakdown", sa.Text(), nullable=True, comment="按目录明细 JSON: [{bucket,files,bytes}]"
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 回收站条目（文件本体在 uploads/.trash/<batch>/<rel_path>，可还原）
    op.create_table(
        "media_trash_entries",
        sa.Column(
            "rel_path", sa.String(length=512), nullable=False, comment="uploads 下原相对路径"
        ),
        sa.Column("bucket", sa.String(length=32), nullable=False, comment="一级目录（统计用）"),
        sa.Column("bytes", sa.BigInteger(), nullable=False, comment="文件字节"),
        sa.Column("batch", sa.String(length=32), nullable=False, comment="批次目录名（时间戳）"),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            comment="trashed/restored/purged",
        ),
        sa.Column("actor_id", sa.Integer(), nullable=True, comment="操作用户 id"),
        sa.Column("actor_name", sa.String(length=64), nullable=False, comment="操作用户显示名"),
        sa.Column(
            "restore_until", sa.DateTime(), nullable=True, comment="保留截止（到期复检后再清除）"
        ),
        sa.Column("restored_at", sa.DateTime(), nullable=True, comment="还原时间"),
        sa.Column("restored_by", sa.Integer(), nullable=True, comment="还原人 id"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_media_trash_entries_rel_path"),
        "media_trash_entries",
        ["rel_path"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_media_trash_entries_rel_path"), table_name="media_trash_entries")
    op.drop_table("media_trash_entries")
    op.drop_table("media_censuses")
