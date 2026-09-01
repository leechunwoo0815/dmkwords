# tests/unit/test_wm6_login_guard.py — P0-F2：登录验证码 fail-closed
"""修复语义：LOGIN_DEV_CODE 配置化（.env 可覆盖）；空配置 = 任何 code 全拒（fail-closed）；
validate_production 硬校验非空 → 生产启动失败（逼 WM12 接真实通道）。
dev 默认 "1234" 流程不变（.env 不加 LOGIN_DEV_CODE）。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from backend.config import get_settings

    yield
    get_settings.cache_clear()


def _login(client: TestClient, phone="13800002201", code="1234"):
    client.post(
        "/api/admin/members/parents",
        json={"name": "验证码家长", "phone": phone},
        headers=_h(client),
    )
    return client.post("/api/miniapp/login", json={"phone": phone, "code": code})


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_empty_dev_code_rejects_all(client: TestClient, monkeypatch):
    """fail-closed：LOGIN_DEV_CODE 置空（模拟生产）→ 任何 code 全拒 422。
    （修复前 login 不读配置，code=1234 恒 200——fail-closed 语义缺失即 RED。）"""
    from backend.config import get_settings

    monkeypatch.setattr(
        get_settings.cache_clear.__wrapped__
        if hasattr(get_settings, "cache_clear")
        else get_settings,
        "_x",
        None,
        raising=False,
    ) if False else None
    monkeypatch.setenv(
        "LOGIN_DEV_CODE", ""
    )  # BaseSettings 读 env：修复前字段不存在被忽略（1234 恒过 = RED）；修复后读到空串全拒
    get_settings.cache_clear()
    r = _login(client, code="1234")
    assert r.status_code == 422, f"空配置仍放行: {r.status_code} {r.text[:120]}"
    r2 = _login(client, phone="13800002202", code="0000")
    assert r2.status_code == 422


def test_default_dev_code_unchanged(client: TestClient):
    """防误伤：默认配置（dev 不加 .env 覆盖）code=1234 流程照常 200。"""
    r = _login(client)
    assert r.status_code == 200, r.text
    assert "token" in r.json()


def test_validate_production_rejects_non_empty_dev_code(monkeypatch):
    """生产硬校验：DEBUG=false + LOGIN_DEV_CODE 非空 → RuntimeError 含该字段名。"""
    from backend.config import get_settings

    monkeypatch.setenv("LOGIN_DEV_CODE", "1234")  # 修复前字段不存在不读 env（不抛 = RED）
    monkeypatch.setenv("DEBUG", "false")
    get_settings.cache_clear()
    s = get_settings()
    try:
        s.validate_production()
    except RuntimeError as e:
        assert "LOGIN_DEV_CODE" in str(e)
    else:
        raise AssertionError("validate_production 未拦截非空 LOGIN_DEV_CODE（fail-open）")


def test_validate_production_passes_when_empty(monkeypatch):
    """置空 + 其余生产项合规 → 不因验证码通道报错（fail-closed 不误伤合规部署）。"""
    from backend.config import get_settings

    monkeypatch.setenv("LOGIN_DEV_CODE", "")
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("DB_PASSWORD", "pw")
    monkeypatch.setenv("WECHAT_APP_ID", "wx")
    monkeypatch.setenv("WECHAT_APP_SECRET", "sec")
    get_settings.cache_clear()
    s = get_settings()
    s.validate_production()  # 不抛即过
