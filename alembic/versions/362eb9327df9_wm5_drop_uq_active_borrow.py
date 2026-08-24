"""wm5 drop uq_active_borrow

Revision ID: 362eb9327df9
Revises: 4898c3d47de4
Create Date: 2026-08-22 20:37:49.643741

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "362eb9327df9"
down_revision: Union[str, Sequence[str], None] = "4898c3d47de4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 补全说明（2026-08-24 CI 首绿暴露）：本迁移原为 autogenerate 空壳
    # （本地库曾手工 DROP 索引导致 autogen 无 diff），模型一直无此索引。
    # 该唯一索引 (copy_id, is_deleted) 会拦住"还书后再次借阅同一副本"
    # （return_book 不改 is_deleted），真实业务缺陷，干净库重建即复现。
    op.drop_index("uq_active_borrow", table_name="borrow_records")


def downgrade() -> None:
    """Downgrade schema."""
    # 对称可逆（索引结构补回 4898c3d47de4 创建时的定义）
    op.create_index(
        "uq_active_borrow",
        "borrow_records",
        ["copy_id", "is_deleted"],
        unique=True,
    )
