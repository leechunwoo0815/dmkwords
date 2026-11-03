# tests/unit/test_batch162_config_20261103.py — 后台 30 天登录 + 音频上传 20MB
"""2026-11-03 甲方裁定（第一百六十二批）：

1. **后台登录有效期 30 天（720h）**：内部 1–2 人使用，不想每天输密码。
2. **多设备同时登录不互踢**：登录**不得** bump `token_generation`——否则第二台设备
   登录会把第一台踢下线（改密码才该全端失效，那条语义仍在别的判据里）。
3. **音频上传上限 20MB**（原 50MB）：有声书 64kbps 单声道足够，省磁盘与内存。

本判据锁三处：种子默认值、`settings` 回落默认、登录不 bump gen（两台设备 token 同时可用）。
"""

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.unit.test_wm10_concurrency import _db, _h


def test_seed_defaults_are_30_days_and_20mb():
    """老库靠数据迁移 `b7e2c4a9d1f5`；新库靠种子默认值。"""
    from backend.seeds.seed_configs import CONFIG_SEEDS

    seeds = {row[0]: row[1] for row in CONFIG_SEEDS}
    assert seeds["admin_token_expire_hours"] == "720"
    assert seeds["audio_upload_max_mb"] == "20"


def test_settings_fallback_default_is_30_days():
    """配置中心缺键/非法值时的回落默认（`AuthService._token_expire_hours`）。"""
    from backend.config import get_settings

    assert get_settings().ADMIN_TOKEN_EXPIRE_HOURS == 720


def test_audio_upload_limit_default_20mb():
    """读进内存前拦截：19MB 放行、20MB+1 字节拒绝，报错带新上限。"""
    from backend.common.exceptions import ValidationError
    from backend.common.file_storage import ensure_upload_size_within_limit

    ok = types.SimpleNamespace(size=19 * 1024 * 1024)
    ensure_upload_size_within_limit(ok, key="audio_upload_max_mb", default_mb=20, label="音频文件")

    big = types.SimpleNamespace(size=20 * 1024 * 1024 + 1)
    with pytest.raises(ValidationError) as exc:
        ensure_upload_size_within_limit(
            big, key="audio_upload_max_mb", default_mb=20, label="音频文件"
        )
    assert "音频文件体积超限" in str(exc.value)
    assert "20MB" in str(exc.value)


def test_two_admin_tokens_coexist_without_kick(client: TestClient):
    """多设备：两次登录都可用，`token_generation` 不变（不做单点登录互踢）。"""
    h1 = _h(client)
    db = _db()
    try:
        gen1 = db.execute(
            text("SELECT token_generation FROM admin_users WHERE username = 'admin'")
        ).scalar()
    finally:
        db.close()

    h2 = _h(client)
    db = _db()
    try:
        gen2 = db.execute(
            text("SELECT token_generation FROM admin_users WHERE username = 'admin'")
        ).scalar()
    finally:
        db.close()

    assert gen1 == gen2, "登录不得 bump token_generation（否则先登录的设备被踢）"

    # 第一台设备的 token 在第二次登录后仍然可用
    for h in (h1, h2):
        r = client.get("/api/admin/configs", headers=h)
        assert r.status_code == 200, r.text
