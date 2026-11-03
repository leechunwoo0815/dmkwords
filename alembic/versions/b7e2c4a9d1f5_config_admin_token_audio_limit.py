"""config_admin_token_audio_limit

Revision ID: b7e2c4a9d1f5
Revises: f1b3d5e7a9c2
Create Date: 2026-11-03 10:00:00.000000

2026-11-03 甲方裁定（第一百六十二批）：

- 后台登录有效期 8h → **720h（30 天）**：内部 1–2 人使用，多设备同时登录不互踢；
  改密码仍会 bump `token_generation` 让全端失效。
- 音频上传上限 50MB → **20MB**：有声书 64kbps 单声道足够，限制磁盘与内存占用。

种子脚本对已存在的键是「跳过、不覆盖运营期修改值」，所以老库必须靠本数据迁移落地。
只在**当前值仍是旧默认**时改写（运营手工调过的值保持原样，避免覆盖人为决策）。
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c4a9d1f5"
down_revision: Union[str, Sequence[str], None] = "f1b3d5e7a9c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """老库两条配置值升到新默认。"""
    op.execute(
        "UPDATE system_configs "
        "SET config_value = '720', "
        "default_value = CASE WHEN default_value = '8' THEN '720' ELSE default_value END "
        "WHERE config_key = 'admin_token_expire_hours' AND config_value = '8'"
    )
    op.execute(
        "UPDATE system_configs "
        "SET config_value = '20', "
        "default_value = CASE WHEN default_value = '50' THEN '20' ELSE default_value END "
        "WHERE config_key = 'audio_upload_max_mb' AND config_value = '50'"
    )


def downgrade() -> None:
    """回退到旧默认（仅当值仍是本迁移写入的新默认）。"""
    op.execute(
        "UPDATE system_configs "
        "SET config_value = '8', "
        "default_value = CASE WHEN default_value = '720' THEN '8' ELSE default_value END "
        "WHERE config_key = 'admin_token_expire_hours' AND config_value = '720'"
    )
    op.execute(
        "UPDATE system_configs "
        "SET config_value = '50', "
        "default_value = CASE WHEN default_value = '20' THEN '50' ELSE default_value END "
        "WHERE config_key = 'audio_upload_max_mb' AND config_value = '20'"
    )
