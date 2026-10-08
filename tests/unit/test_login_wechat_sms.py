# tests/unit/test_login_wechat_sms.py — 登录接线（上线前审查 P0-1 整改的固化断言）
"""三条通道各自成测：

① 微信 openid（**主通道**）：code2session 用 monkeypatch 顶掉（不打真微信接口），
   走真 HTTP 验"未绑 → need_bind + 绑定凭证 → 绑手机号 → 发登录态 → 下次直接登录"；
② 短信兜底：真发（mock 网关）→ 真落库哈希 → 真校验（限流/过期/错误次数/一次性）；
③ 开发固定码：仅当 `LOGIN_DEV_CODE` 非空（生产置空即 fail-closed，见 config 校验测）。

以及一条"生产不许用 mock 短信网关"的配置校验断言。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.common.gateways.sms.mock import MockSmsGateway


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _parent_with_child(client, phone, name="登录测试家长"):
    h = _h(client)
    p = client.post("/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h)
    assert p.status_code == 200, p.text
    c = client.post(
        f"/api/admin/members/parents/{p.json()['id']}/children",
        json={"name": "登录测试孩"},
        headers=h,
    )
    assert c.status_code == 200, c.text
    return p.json(), c.json()


def _clear_rate_limit() -> None:
    """清进程内限流桶——生产限流原样保留（有独立用例验它），测试连打需显式重置。

    同 conftest 的 autouse 做法：限流器是**进程全局**的，一个用例里连打多次会被自家人限住。
    """
    from backend.middleware.rate_limit import _limiter

    _limiter._requests.clear()


def _allow_resend(db) -> None:
    """把"同号发送间隔"临时调 0——一个用例内需要给同一手机号发两次码（默认 60 秒是真限流）。"""
    from backend.common.config_service import ConfigService, invalidate_config_cache
    from tests.unit.test_audit_batch_a import _admin_row

    ConfigService(db).update_config(
        _admin_row(db), "sms_send_interval_seconds", "0", "测试：允许同号重发"
    )
    invalidate_config_cache()


def _send_and_read_code(client, phone, purpose="login") -> str:
    r = client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": purpose})
    assert r.status_code == 200, r.text
    code = MockSmsGateway.get_code(phone)
    assert code, "mock 网关应能读到刚生成的验证码"
    return code


# ---------------- ① 微信主通道 ----------------


def test_wechat_login_bind_then_login(client: TestClient, monkeypatch):
    phone = "13800008201"
    parent, _child = _parent_with_child(client, phone)

    async def _fake_session(code: str) -> dict:
        assert code, "小程序传来的 code 不应为空"
        return {"openid": "oTest_8201", "session_key": "sk"}

    monkeypatch.setattr(
        "backend.integrations.wechat.auth.WeChatAuth.code_to_session",
        staticmethod(_fake_session),
    )

    # 首次：未绑 → need_bind + 绑定凭证（不是登录态）
    r = client.post("/api/miniapp/login/wechat", json={"code": "wx-code-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["need_bind"] is True and body["bind_ticket"]
    assert "token" not in body

    # 绑手机号：短信码（purpose=bind）+ 已建档手机号
    code = _send_and_read_code(client, phone, purpose="bind")
    bind = client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": body["bind_ticket"], "phone": phone, "code": code},
    )
    assert bind.status_code == 200, bind.text
    assert bind.json()["parent"]["id"] == parent["id"]
    assert bind.json()["children"], "登录载荷必须带孩子列表"
    token = bind.json()["token"]

    # openid 已落库 → 再登录直接发登录态
    again = client.post("/api/miniapp/login/wechat", json={"code": "wx-code-2"})
    assert again.status_code == 200, again.text
    assert again.json()["token"]
    assert again.json()["parent"]["id"] == parent["id"]

    # 该 token 能访问受保护端点
    me = client.get("/api/miniapp/children", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["children"]


def test_wechat_bind_rejects_wrong_phone_and_rebind(client: TestClient, monkeypatch, db):
    # 用一个可切换的假 code2session：state["openid"] 换一下就是"另一个微信号"
    state = {"openid": "oTest_8202"}

    async def _fake_session(code: str) -> dict:
        return {"openid": state["openid"]}

    monkeypatch.setattr(
        "backend.integrations.wechat.auth.WeChatAuth.code_to_session",
        staticmethod(_fake_session),
    )
    _parent_with_child(client, "13800008202", name="绑A")
    _allow_resend(db)  # 本用例要给同一手机号发两次码

    ticket = client.post("/api/miniapp/login/wechat", json={"code": "c"}).json()["bind_ticket"]

    # 未建档手机号 → 拒（家长端不开放自助注册）
    code = _send_and_read_code(client, "13800008299", purpose="bind")
    r = client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": ticket, "phone": "13800008299", "code": code},
    )
    assert r.status_code == 422 and "未注册" in r.json()["detail"]

    # 正常绑定：openid oTest_8202 ← 手机号 13800008202
    code_ok = _send_and_read_code(client, "13800008202", purpose="bind")
    ok = client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": ticket, "phone": "13800008202", "code": code_ok},
    )
    assert ok.status_code == 200, ok.text
    # 绑好了：同一 openid 再登录直接发登录态（不再 need_bind）
    assert "token" in client.post("/api/miniapp/login/wechat", json={"code": "c"}).json()

    # 另一个微信号想绑同一个（已被占用的）手机号 → 拒（防顶号）
    state["openid"] = "oTest_8203"
    ticket2 = client.post("/api/miniapp/login/wechat", json={"code": "c"}).json()["bind_ticket"]
    code2 = _send_and_read_code(client, "13800008202", purpose="bind")
    rebind = client.post(
        "/api/miniapp/login/bind",
        json={"bind_ticket": ticket2, "phone": "13800008202", "code": code2},
    )
    assert rebind.status_code == 422 and "已绑定" in rebind.json()["detail"]


def test_bind_ticket_cannot_be_used_as_login_token(client: TestClient, monkeypatch):
    """绑定凭证 type=bind，不能当家长登录态使（防越权）。"""

    async def _fake_session(code: str) -> dict:
        return {"openid": "oTest_8203"}

    monkeypatch.setattr(
        "backend.integrations.wechat.auth.WeChatAuth.code_to_session",
        staticmethod(_fake_session),
    )
    ticket = client.post("/api/miniapp/login/wechat", json={"code": "c"}).json()["bind_ticket"]
    r = client.get("/api/miniapp/children", headers={"Authorization": f"Bearer {ticket}"})
    assert r.status_code == 401


# ---------------- ② 短信兜底 ----------------


def test_sms_login_end_to_end_and_one_time(client: TestClient):
    phone = "13800008204"
    _parent_with_child(client, phone)
    code = _send_and_read_code(client, phone)

    first = client.post("/api/miniapp/login", json={"phone": phone, "code": code})
    assert first.status_code == 200, first.text
    assert first.json()["token"]

    # 一次性：同一个码不能再用
    again = client.post("/api/miniapp/login", json={"phone": phone, "code": code})
    assert again.status_code == 422


def test_sms_send_interval_and_daily_limit(client: TestClient, db):
    phone = "13800008205"
    _parent_with_child(client, phone)
    assert (
        client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": "login"}).status_code
        == 200
    )
    # 同号 60 秒内第二次 → 拒（409 冲突）
    second = client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": "login"})
    assert second.status_code == 409 and "频繁" in second.json()["detail"]

    # 把间隔改成 0 → 可连发；再把每日上限压到 2 → 第 3 条被拦
    from backend.common.config_service import ConfigService, invalidate_config_cache
    from tests.unit.test_audit_batch_a import _admin_row

    _allow_resend(db)
    ConfigService(db).update_config(_admin_row(db), "sms_daily_limit", "2", "测试：日限")
    invalidate_config_cache()
    assert (
        client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": "login"}).status_code
        == 200
    )
    third = client.post("/api/miniapp/sms/send", json={"phone": phone, "purpose": "login"})
    assert third.status_code == 409 and "上限" in third.json()["detail"]


def test_sms_wrong_code_attempts_limited(client: TestClient):
    phone = "13800008206"
    _parent_with_child(client, phone)
    real = _send_and_read_code(client, phone)
    wrong = "000000" if real != "000000" else "111111"
    for i in range(5):
        _clear_rate_limit()  # 登录端点另有 3 次/分钟 IP 限流（见 test_login_endpoint_rate_limited）
        r = client.post("/api/miniapp/login", json={"phone": phone, "code": wrong})
        assert r.status_code == 422, r.text
        assert "验证码错误" in r.json()["detail"]
        if i < 4:
            assert f"还可尝试 {4 - i} 次" in r.json()["detail"]
    _clear_rate_limit()
    # 第 6 次：错误次数用尽（即使拿对的码也不放行，必须重新获取）
    assert (
        client.post("/api/miniapp/login", json={"phone": phone, "code": wrong})
        .json()["detail"]
        .startswith("验证码错误次数过多")
    )
    _clear_rate_limit()
    assert client.post("/api/miniapp/login", json={"phone": phone, "code": real}).status_code == 422


def test_login_endpoint_rate_limited(client: TestClient):
    """登录端点 3 次/分钟（IP 维度）：连打第 4 次 429 —— 撞库防护，不是 bug。"""
    phone = "13800008209"
    _parent_with_child(client, phone)
    real = _send_and_read_code(client, phone)
    # 故意不清限流桶：3 次连打全部计入（第 2、3 次因验证码一次性而 422，但都算请求数）
    for _ in range(3):
        client.post("/api/miniapp/login", json={"phone": phone, "code": real})
    assert client.post("/api/miniapp/login", json={"phone": phone, "code": real}).status_code == 429


def test_sms_code_expires(client: TestClient, db):
    phone = "13800008207"
    _parent_with_child(client, phone)
    code = _send_and_read_code(client, phone)
    from backend.domain.identity.models import SmsCode

    row = db.query(SmsCode).filter(SmsCode.phone == phone).order_by(SmsCode.id.desc()).first()
    row.expires_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": code})
    assert r.status_code == 422 and "过期" in r.json()["detail"]


def test_dev_fixed_code_still_works_only_because_dev_code_set(client: TestClient):
    """开发固定码通道：本环境 LOGIN_DEV_CODE=1234 → 可用（生产置空即自动关闭）。"""
    phone = "13800008208"
    _parent_with_child(client, phone)
    assert (
        client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"}).status_code == 200
    )


# ---------------- ③ 配置校验（生产不许 mock 网关） ----------------


def test_production_rejects_mock_sms_gateway():
    from backend.config import Settings

    base = dict(
        DEBUG=False,
        SECRET_KEY="real-secret",
        DB_PASSWORD="pw",
        WECHAT_APP_ID="wx",
        WECHAT_APP_SECRET="sk",
        LOGIN_DEV_CODE="",
        CORS_ORIGINS="https://admin.example.com",
        # 本用例只考短信通道：线上支付关掉，免得支付四件套校验掺进来（另有专门用例）
        PAYMENT_ENABLED=False,
    )
    with pytest.raises(RuntimeError) as exc:
        Settings(SMS_ENABLED=True, SMS_PROVIDER="mock", **base).validate_production()
    assert "SMS_PROVIDER" in str(exc.value)
    # 关掉短信（纯微信登录）→ 放行
    Settings(SMS_ENABLED=False, SMS_PROVIDER="mock", **base).validate_production()
    # 接真实网关但缺凭据 → 拒
    with pytest.raises(RuntimeError) as exc2:
        Settings(SMS_ENABLED=True, SMS_PROVIDER="aliyun", **base).validate_production()
    assert "凭据" in str(exc2.value)
