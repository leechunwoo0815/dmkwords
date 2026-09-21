"""borrow console member_code

Revision ID: b4d8e2f6a1c5
Revises: f2b8d4a6c1e3
Create Date: 2026-09-21 11:40:00.000000

借阅操作台扫码闭环 A 批（2026-09-21 任务包）：`children` 加 `member_code`
——"运营人员拿出扫码枪，扫描小程序端的孩子会员码，每个孩子都有不同的会员码"。

为什么不用自增 id 当码：可枚举（照着数字顺序试就能扮成别的孩子）。本码 = `M` + 8 位随机 +
1 位校验，字母表剔除易混字符（0/O、1/I/L）。

数据回填：**存量孩子必须补码**，否则演示现场/历史账号在小程序上出示不出码。
回填算法**内联在本文件**（不 import `backend.domain.identity.member_code`）——
迁移是历史快照，不能随应用代码演进而改变行为。

downgrade：删索引 + 删列（回滚后借阅台只按姓名/手机号查孩子，其余行为不变）。
"""

from collections.abc import Sequence
import secrets

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d8e2f6a1c5"
down_revision: str | Sequence[str] | None = "f2b8d4a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ↓↓ 与 backend/domain/identity/member_code.py 同算法的**冻结副本**（勿改） ↓↓
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _gen() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    total = sum((i + 1) * _ALPHABET.index(ch) for i, ch in enumerate(body))
    return f"M{body}{_ALPHABET[total % len(_ALPHABET)]}"


# ↑↑ 冻结副本结束 ↑↑


def upgrade() -> None:
    op.add_column(
        "children",
        sa.Column(
            "member_code",
            sa.String(length=12),
            nullable=True,
            comment="会员码（M+8 位随机+校验位，扫码识别用，不可枚举）",
        ),
    )
    op.create_index("uq_child_member_code", "children", ["member_code", "is_deleted"], unique=True)

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM children WHERE member_code IS NULL")).fetchall()
    for (child_id,) in rows:
        conn.execute(
            sa.text("UPDATE children SET member_code = :code WHERE id = :cid"),
            {"code": _gen(), "cid": child_id},
        )


def downgrade() -> None:
    op.drop_index("uq_child_member_code", table_name="children")
    op.drop_column("children", "member_code")
