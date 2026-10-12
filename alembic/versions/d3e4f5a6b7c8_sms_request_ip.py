"""sms_codes.request_ip —— 单 IP 日上限防刷（2026-10-12 拍板 P2）

背景：发码接口不需要登录，原口径只按手机号记当日额度——任何人拿一个号反复调用就能把
**受害者**当天的额度烧光（没绑微信的家长当天彻底登不进来，短信费还由我们承担）。
本迁移给 `sms_codes` 加来源 IP 列（+索引），服务层按"同 IP 当日条数"再加一层总量闸。

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sms_codes",
        sa.Column(
            "request_ip",
            sa.String(length=45),
            nullable=True,
            comment="请求来源 IP（2026-10-12：单 IP 日上限防刷）",
        ),
    )
    op.create_index("ix_sms_codes_request_ip", "sms_codes", ["request_ip"])


def downgrade() -> None:
    op.drop_index("ix_sms_codes_request_ip", table_name="sms_codes")
    op.drop_column("sms_codes", "request_ip")
