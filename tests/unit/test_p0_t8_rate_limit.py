# tests/unit/test_p0_t8_rate_limit.py — P0 第一批 T8（I-3+C-1/2）限流接线 + 种子弱口令生产拦截
"""红测试：
1. admin 登录连续 6 次错误 → 第 6 次 429（rate_limit(5,60) 接线前不拦 = RED）
2. 家长验证码登录连续 4 次 → 第 4 次 429（rate_limit(3,60)）
3. seed_admin 生产模式（非 DEBUG）拒绝默认弱口令（接线前不拦 = RED）
"""

import pytest
from fastapi.testclient import TestClient


def test_admin_login_rate_limited(client: TestClient):
    """修复前：连续错误登录无 429（RED）。"""
    for _ in range(5):
        r = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code in (401, 422), f"错误密码应 401，实 {r.status_code}"
    r6 = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
    assert r6.status_code == 429, f"第 6 次错误登录应 429 限流，实 {r6.status_code} {r6.text[:80]}"


def test_miniapp_login_rate_limited(client: TestClient):
    """修复前：家长验证码登录无 429（RED）。"""
    for _ in range(3):
        r = client.post("/api/miniapp/login", json={"phone": "13911112222", "code": "wrong"})
        assert r.status_code in (401, 422)
    r4 = client.post("/api/miniapp/login", json={"phone": "13911112222", "code": "wrong"})
    assert r4.status_code == 429, f"第 4 次登录应 429 限流，实 {r4.status_code} {r4.text[:80]}"


def test_seed_admin_production_rejects_default_password(monkeypatch):
    """生产拒播种弱口令——判据是 **APP_ENV**（P1-12，2026-10-09 由 DEBUG 改口径）。

    旧语义"非 DEBUG 即生产"有个洞：`.env.example` 默认 `DEBUG=true`，操作员照抄模板
    就能把 `admin/dmkwords123` 播种进生产（启动校验只看支付/短信/CORS/SECRET_KEY，不拦）。
    现在与 `validate_production` 同一开关（APP_ENV），断言不变：生产必须拒。
    """
    from backend.config import get_settings
    from backend.seeds import seed_admin

    s = get_settings()
    monkeypatch.setattr(s, "APP_ENV", "production")
    with pytest.raises(RuntimeError):
        seed_admin.seed()
