"""wm8 vocabulary favorites dictionary tables

Revision ID: b8e2f5a7c1d9
Revises: a7f1c3d9b2e4
Create Date: 2026-08-22 22:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8e2f5a7c1d9"
down_revision: Union[str, Sequence[str], None] = "a7f1c3d9b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dictionary_words",
        sa.Column("word", sa.String(length=64), nullable=False, comment="词条（小写）"),
        sa.Column("phonetic", sa.String(length=128), nullable=True, comment="音标"),
        sa.Column("definition", sa.Text(), nullable=True, comment="英文释义"),
        sa.Column("translation", sa.Text(), nullable=True, comment="中文翻译"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dictionary_words_word"), "dictionary_words", ["word"], unique=False)

    op.create_table(
        "vocabularies",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("word", sa.String(length=64), nullable=False, comment="单词"),
        sa.Column("book_id", sa.Integer(), nullable=True, comment="来源书目（查词时正在听的书）"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_vocab_child_word", "vocabularies", ["child_id", "word"], unique=True)
    op.create_index(op.f("ix_vocabularies_child_id"), "vocabularies", ["child_id"], unique=False)

    op.create_table(
        "favorites",
        sa.Column("child_id", sa.Integer(), nullable=False),
        sa.Column("book_id", sa.Integer(), nullable=False, comment="书目"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_fav_child_book", "favorites", ["child_id", "book_id"], unique=True)
    op.create_index(op.f("ix_favorites_child_id"), "favorites", ["child_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_favorites_child_id"), table_name="favorites")
    op.drop_index("uq_fav_child_book", table_name="favorites")
    op.drop_table("favorites")
    op.drop_index(op.f("ix_vocabularies_child_id"), table_name="vocabularies")
    op.drop_index("uq_vocab_child_word", table_name="vocabularies")
    op.drop_table("vocabularies")
    op.drop_index(op.f("ix_dictionary_words_word"), table_name="dictionary_words")
    op.drop_table("dictionary_words")
