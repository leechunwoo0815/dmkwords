# tests/unit/test_miniapp_demo_login_20261112.py — 只读「演示体验」登录（微信审核整改）
"""背景（2026-11-12 微信审核驳回）：审核员没有本店账号，用微信手机号快速验证只会拿到
「该手机号未注册（请到店建档）」，于是连核心页面都进不去（驳回原因 ②）。

本批加 `POST /api/miniapp/login/demo`：按 `.env` 的 `DEMO_LOGIN_PHONE` 找演示家长，
签发带 `demo:true` 的 token。**只读边界必须由服务端保证**（不能只靠前端隐藏按钮）：
`get_current_parent` 对 demo token 的非 GET/HEAD/OPTIONS 请求一律 403。

判据（4 条 + 1 条开关）：
  1. `DEMO_LOGIN_PHONE` 空 → 端点 422（不给"能点但一定失败"的入口）；
  2. 配了演示家长 → 200，token 带 `demo`，返回同一家长与其孩子；
  3. demo token 的 GET 正常（能看页面数据）；
  4. demo token 的写请求 403「演示模式仅供浏览」；
  5. `/login/options` 的 `demo_enabled` 跟随开关（前端据此显示/隐藏按钮）。
"""

from __future__ import annotations

import jwt as pyjwt
import pytest

from backend.config import get_settings


@pytest.fixture
def demo_phone(monkeypatch):
    """把 `DEMO_LOGIN_PHONE` 设成指定手机号。

    `get_settings()` 是 `@lru_cache` 的（进程内单例）——只 setenv 不清缓存，
    端点里读到的仍是导入时那份 Settings（首批实测：一直 422「演示模式未开启」）。
    测试结束再清一次，避免把开关漏给后面的用例。
    """

    def _set(phone: str) -> None:
        monkeypatch.setenv("DEMO_LOGIN_PHONE", phone)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent_with_child(client, phone, name="演示测试家长"):
    h = _h(client)
    p = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert p.status_code == 200, p.text
    c = client.post(
        f"/api/admin/members/parents/{p.json()['id']}/children",
        json={"name": "演示测试孩"},
        headers=h,
    )
    assert c.status_code == 200, c.text
    return p.json(), c.json()


def _clear_rate_limit() -> None:
    from backend.middleware.rate_limit import _limiter

    _limiter._requests.clear()


def test_demo_disabled_by_default(client):
    """没配 DEMO_LOGIN_PHONE → 422，且 options 的 demo_enabled=false。"""
    _clear_rate_limit()
    assert get_settings().DEMO_LOGIN_PHONE == ""
    r = client.post("/api/miniapp/login/demo", json={})
    assert r.status_code == 422, r.text
    assert "演示模式未开启" in r.json()["detail"]

    opt = client.get("/api/miniapp/login/options")
    assert opt.status_code == 200
    assert opt.json()["demo_enabled"] is False


def test_demo_login_returns_demo_token(client, demo_phone):
    """配了演示家长 → 登录成功，token 带 demo 声明，返回该家长与孩子。"""
    _clear_rate_limit()
    parent, child = _parent_with_child(client, "13900001111")
    demo_phone("13900001111")

    opt = client.get("/api/miniapp/login/options")
    assert opt.json()["demo_enabled"] is True

    r = client.post("/api/miniapp/login/demo", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo"] is True
    assert body["parent"]["id"] == parent["id"]
    assert [c["id"] for c in body["children"]] == [child["id"]]

    payload = pyjwt.decode(body["token"], get_settings().SECRET_KEY, algorithms=["HS256"])
    assert payload["demo"] is True
    assert payload["type"] == "parent"
    assert payload["sub"] == str(parent["id"])


def test_demo_login_unknown_phone(client, demo_phone):
    """开关指向一个没建档的手机号 → 422，文案指向馆员（不是 500）。"""
    _clear_rate_limit()
    demo_phone("13900009999")
    r = client.post("/api/miniapp/login/demo", json={})
    assert r.status_code == 422, r.text
    assert "演示账号未建档" in r.json()["detail"]


def test_demo_token_is_read_only(client, demo_phone):
    """GET 放行；任何写操作 403（演示账号不能下预约/点赞，污染生产数据）。"""
    _clear_rate_limit()
    _parent_with_child(client, "13900002222")
    demo_phone("13900002222")
    tok = client.post("/api/miniapp/login/demo", json={}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    ok = client.get("/api/miniapp/children", headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["children"]

    # 写：阅读进度上报（POST，鉴权依赖先于 body 校验解析 → 一定能拿到 403）
    write = client.post("/api/miniapp/reading/progress", json={}, headers=h)
    assert write.status_code == 403, write.text
    assert "演示模式仅供浏览" in write.json()["detail"]
