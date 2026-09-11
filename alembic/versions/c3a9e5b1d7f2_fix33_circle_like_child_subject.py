"""fix33 circle like child subject

Revision ID: c3a9e5b1d7f2
Revises: a1d7f3c9e5b8
Create Date: 2026-09-11 20:10:00.000000

fix33 R2（专家裁决：社交主体彻底切到孩子，家长不参与）：
① 新增 `child_id` 列，唯一索引 `uq_circle_like` 由 (post_id, parent_id) 改为
   (post_id, child_id) —— 兄弟可各赞各的（旧口径同家长多孩互相顶不掉）；
② 历史数据：存量行 `child_id` ← `liker_child_id` 回填；`liker_child_id IS NULL`
   的「无孩历史赞」（WM15 前老端产生的赞，头像墙本就不显示）先扣减 `like_count`
   再软删 —— 维持「like_count ≡ 活跃赞行数」不变式，否则 R3 同族的
   「取消点赞后头像墙/计数清不掉」会永久残留。

downgrade 说明：结构可完整回滚（(post,parent) 去重 → 还原旧唯一索引 → 删列）；
**不还原迁移期对 like_count 的扣减** —— DB 无法区分「本次迁移软删的行」与
「迁移前就已软删的行」，反向加回会多计。需回滚时按旧口径
`COUNT(circle_likes WHERE post_id=p AND COALESCE(is_deleted,0)=0)` 人工重算计数。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3a9e5b1d7f2"
down_revision: str | Sequence[str] | None = "a1d7f3c9e5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHILD_ID_COMMENT = "社交主体=点赞孩子ID（活跃行必非空；历史无孩赞已软删保留 NULL）"
LIKER_CHILD_ID_COMMENT_V2 = "WM15 名义快照列（fix33 起与 child_id 同值保留，仅供 downgrade 回滚）"
LIKER_CHILD_ID_COMMENT_V1 = "点赞方当时选中的孩子（展示名义快照；旧数据/未传为 NULL）"


def _migrate_like_subject(conn) -> None:
    """历史数据迁移（独立函数：迁移与单测跑**同一段 SQL**，避免口径漂移）。

    幂等：回填只写「child_id 为空且有名义」的行；扣减/软删只针对「活跃且无孩」
    的行，第二次执行命中 0 行。
    """
    # ① 名义快照 → 社交主体
    conn.execute(
        sa.text(
            "UPDATE circle_likes SET child_id = liker_child_id "
            "WHERE child_id IS NULL AND liker_child_id IS NOT NULL"
        )
    )
    # ② 无孩历史赞：先从帖子计数里摘掉（0 下限），再软删
    conn.execute(
        sa.text(
            "UPDATE circle_posts p JOIN ("
            "  SELECT post_id, COUNT(*) AS n FROM circle_likes"
            "  WHERE child_id IS NULL AND COALESCE(is_deleted, 0) = 0 GROUP BY post_id"
            ") x ON x.post_id = p.id "
            "SET p.like_count = GREATEST(p.like_count - x.n, 0)"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE circle_likes SET is_deleted = 1 "
            "WHERE child_id IS NULL AND COALESCE(is_deleted, 0) = 0"
        )
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "circle_likes",
        sa.Column("child_id", sa.Integer(), nullable=True, comment=CHILD_ID_COMMENT),
    )
    op.create_index(op.f("ix_circle_likes_child_id"), "circle_likes", ["child_id"], unique=False)
    # 名义快照列口径变更 → 同步列注释（alembic check 对拍列注释，不改会报差异）
    op.alter_column(
        "circle_likes",
        "liker_child_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment=LIKER_CHILD_ID_COMMENT_V2,
    )
    # 先修数据再换索引（存量 (post, child) 由旧 (post, parent) 唯一性天然不重复）
    _migrate_like_subject(op.get_bind())
    op.drop_index("uq_circle_like", table_name="circle_likes")
    op.create_index("uq_circle_like", "circle_likes", ["post_id", "child_id"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    # 旧口径 (post_id, parent_id) 唯一：兄弟多赞必须**物理**去重（软删行也占旧索引）。
    # 同组保留优先级：活跃 > 已软删，其次 id 小者胜。
    op.execute(
        sa.text(
            "DELETE l1 FROM circle_likes l1 JOIN circle_likes l2 "
            "  ON l1.post_id = l2.post_id AND l1.parent_id = l2.parent_id "
            " AND (COALESCE(l1.is_deleted, 0) > COALESCE(l2.is_deleted, 0) "
            "      OR (COALESCE(l1.is_deleted, 0) = COALESCE(l2.is_deleted, 0) "
            "          AND l1.id > l2.id))"
        )
    )
    op.drop_index("uq_circle_like", table_name="circle_likes")
    op.create_index("uq_circle_like", "circle_likes", ["post_id", "parent_id"], unique=True)
    op.alter_column(
        "circle_likes",
        "liker_child_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment=LIKER_CHILD_ID_COMMENT_V1,
    )
    op.drop_index(op.f("ix_circle_likes_child_id"), table_name="circle_likes")
    op.drop_column("circle_likes", "child_id")
