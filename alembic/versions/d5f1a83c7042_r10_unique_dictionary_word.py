"""dictionary_words.word 改唯一索引（第十轮审查 W3-03）

Revision ID: d5f1a83c7042
Revises: 3eeb0406e90b
Create Date: 2026-10-24

为什么（P2，审查实测）：
  `scripts/import_ecdict.py` 的 SQL 是 `INSERT IGNORE`，脚本头自述"保留已有词、可随时
  Ctrl-C 重跑续传"；但 `ix_dictionary_words_word` 原先 `unique=False`（普通索引），
  且该表除主键外无其它索引 → **没有任何可冲突的唯一键** ⇒ `INSERT IGNORE` 无对象可忽略，
  第二次执行就是整库再插 340 万词（消费方 `vocabulary_service` 用字典推导只保留最后一条，
  表现为"同一个词释义随机变"）。
  加唯一索引后，`INSERT IGNORE` 的去重语义才真正成立（脚本同时去掉 `unique_checks=0`）。

安全约束：唯一索引建不上就是有重复词——**直接报错中止**，不静默删数据。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f1a83c7042"
down_revision: Union[str, Sequence[str], None] = "3eeb0406e90b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    dups = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ("
            "  SELECT word FROM dictionary_words GROUP BY word HAVING COUNT(*) > 1"
            ") AS dup_words"
        )
    ).scalar()
    if dups:
        raise RuntimeError(
            f"dictionary_words 存在 {dups} 个重复词，无法建唯一索引；"
            "请先人工核对去重（禁止脚本自动删词）"
        )
    op.drop_index(op.f("ix_dictionary_words_word"), table_name="dictionary_words")
    op.create_index(op.f("ix_dictionary_words_word"), "dictionary_words", ["word"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_dictionary_words_word"), table_name="dictionary_words")
    op.create_index(op.f("ix_dictionary_words_word"), "dictionary_words", ["word"], unique=False)
