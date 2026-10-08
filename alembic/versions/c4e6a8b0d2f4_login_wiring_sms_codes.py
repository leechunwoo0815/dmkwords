"""login wiring: sms_codes

Revision ID: c4e6a8b0d2f4
Revises: b3d5f7a9c1e2
Create Date: 2026-10-08 09:00:00.000000

登录接线（上线前审查 P0-1）：微信 openid 主通道 + 短信兜底的**服务端校验表**。
只新增一张表 `sms_codes`——`parents.wechat_openid` 列早已存在（只是从没被写入过）。
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e6a8b0d2f4"
down_revision: str | Sequence[str] | None = "b3d5f7a9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sms_codes",
        sa.Column("phone", sa.String(length=20), nullable=False, comment="手机号"),
        sa.Column(
            "purpose",
            sa.String(length=16),
            nullable=False,
            comment="login/bind",
            server_default="login",
        ),
        sa.Column(
            "code_hash",
            sa.String(length=64),
            nullable=False,
            comment="sha256(phone:code:SECRET_KEY)",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False, comment="过期时间"),
        sa.Column("used_at", sa.DateTime(), nullable=True, comment="使用时间（一次性）"),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            comment="校验失败次数（防爆破）",
            server_default="0",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="发送时间"),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("create_time", sa.DateTime(), nullable=True, comment="创建时间"),
        sa.Column("update_time", sa.DateTime(), nullable=True, comment="更新时间"),
        sa.Column(
            "is_deleted", sa.SmallInteger(), nullable=True, comment="软删除标记: 0=正常 1=已删除"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sms_codes_phone"), "sms_codes", ["phone"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_sms_codes_phone"), table_name="sms_codes")
    op.drop_table("sms_codes")
