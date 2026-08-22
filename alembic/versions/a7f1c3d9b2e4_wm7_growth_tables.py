"""wm7 growth tables

Revision ID: a7f1c3d9b2e4
Revises: 92e6434da8d1
Create Date: 2026-08-22 21:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7f1c3d9b2e4"
down_revision: Union[str, Sequence[str], None] = "92e6434da8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "quiz_attempts",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, comment="答对题数"),
        sa.Column("total_questions", sa.Integer(), nullable=False, comment="总题数"),
        sa.Column("passed", sa.SmallInteger(), nullable=False, comment="1=及格（≥及格线）"),
        sa.Column("snapshot", sa.Text(), nullable=False, comment="题目快照 JSON（改题不影响历史）"),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quiz_attempts_child_id"), "quiz_attempts", ["child_id"], unique=False)
    op.create_index(op.f("ix_quiz_attempts_book_id"), "quiz_attempts", ["book_id"], unique=False)

    op.create_table(
        "words_ledgers",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, comment="该书总词数"),
        sa.Column("source", sa.String(length=20), nullable=False, comment="来源（quiz=测验通过）"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_words_child_book", "words_ledgers", ["child_id", "book_id"], unique=True)
    op.create_index(op.f("ix_words_ledgers_child_id"), "words_ledgers", ["child_id"], unique=False)

    op.create_table(
        "point_ledgers",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, comment="本笔积分（正数）"),
        sa.Column(
            "reason_type",
            sa.String(length=30),
            nullable=False,
            comment="words_convert/quiz_first_pass/quiz_full_marks/checkin_7/checkin_30/manual_adjust",
        ),
        sa.Column("detail", sa.String(length=200), nullable=False, comment="说明（书名/周期等）"),
        sa.Column(
            "related_id", sa.Integer(), nullable=True, comment="关联对象ID（quiz 奖励=book_id）"
        ),
        sa.Column("operator_id", sa.Integer(), nullable=True, comment="操作管理员（人工调整时）"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_point_ledgers_child_id"), "point_ledgers", ["child_id"], unique=False)

    op.create_table(
        "child_growth_states",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("words_total", sa.Integer(), nullable=False, comment="累计有效词数"),
        sa.Column("books_total", sa.Integer(), nullable=False, comment="累计读完本数"),
        sa.Column("points_total", sa.Integer(), nullable=False, comment="累计积分"),
        sa.Column(
            "words_remainder", sa.Integer(), nullable=False, comment="零头池（不满 100 词的余数）"
        ),
        sa.Column("level", sa.String(length=1), nullable=False, comment="当前等级 A-Z（只升不降）"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_growth_child", "child_growth_states", ["child_id", "is_deleted"], unique=True
    )

    op.create_table(
        "milestone_awards",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("node_words", sa.Integer(), nullable=False, comment="节点词数（如 100000）"),
        sa.Column("awarded_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_milestone_child_node", "milestone_awards", ["child_id", "node_words"], unique=True
    )
    op.create_index(
        op.f("ix_milestone_awards_child_id"), "milestone_awards", ["child_id"], unique=False
    )

    op.create_table(
        "checkin_streak_awards",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("cycle_type", sa.String(length=10), nullable=False, comment="days7/days30"),
        sa.Column("cycle_no", sa.Integer(), nullable=False, comment="第几个周期（streak/N）"),
        sa.Column("streak_at", sa.Integer(), nullable=False, comment="达成时的连续天数"),
        sa.Column("awarded_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_streak_award",
        "checkin_streak_awards",
        ["child_id", "cycle_type", "cycle_no"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_streak_award", table_name="checkin_streak_awards")
    op.drop_table("checkin_streak_awards")
    op.drop_index(op.f("ix_milestone_awards_child_id"), table_name="milestone_awards")
    op.drop_index("uq_milestone_child_node", table_name="milestone_awards")
    op.drop_table("milestone_awards")
    op.drop_index("uq_growth_child", table_name="child_growth_states")
    op.drop_table("child_growth_states")
    op.drop_index(op.f("ix_point_ledgers_child_id"), table_name="point_ledgers")
    op.drop_table("point_ledgers")
    op.drop_index(op.f("ix_words_ledgers_child_id"), table_name="words_ledgers")
    op.drop_index("uq_words_child_book", table_name="words_ledgers")
    op.drop_table("words_ledgers")
    op.drop_index(op.f("ix_quiz_attempts_book_id"), table_name="quiz_attempts")
    op.drop_index(op.f("ix_quiz_attempts_child_id"), table_name="quiz_attempts")
    op.drop_table("quiz_attempts")
