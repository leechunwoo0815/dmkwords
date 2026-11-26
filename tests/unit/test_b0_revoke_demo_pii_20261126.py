# tests/unit/test_b0_revoke_demo_pii_20261126.py — 双审查 B0-5 / B0-6 回归（2026-11-26）
"""两份独立深审补充项里的两条「身份」缺陷，都是"看着能用、其实把不该给的东西给了出去"：

B0-5 旧 token 复活：
  家长软删时只写 `is_deleted=1`，不动 `token_generation`；而删档前签发的 token 只校验
  `is_deleted` 与 `gen`。于是馆员"同号再建档"复活同一行之后，**删档前的 token 跟着复活**
  （同一 parents 行、同 gen）——家长还没重新登录，旧手机/旧持有人就能继续看孩子、会员码、订单。
  修法：删档 +1、复活 +1，旧 token 永久 401；复活同时清 `wechat_openid`（旧微信绑定不继承）。

B0-6 演示登录 PII：
  `login_demo` 原先直接复用 `_login_payload`，把一个**真实会员家庭**的家长姓名、手机号、
  孩子姓名/头像、会员码原样交给审核员（截图即外泄；会员码还能在借阅台冒用）。
  修法：`_demo_payload` 统一脱敏；`id` 保持真值（演示 token 要靠它读 GET 数据）。

判据（去修复即红）：
  1. 删档后旧 token 立即 401；
  2. 同号复活后旧 token **仍然** 401；重新登录的新 token 可用；
  3. 复活清空 `wechat_openid`；
  4. demo 载荷手机号呈 `1xx****xxxx`、姓名留首字、会员码打码、头像为空；
  5. 脱敏只作用于载荷——库里手机号仍是真值（后台/客服用）。
"""

from __future__ import annotations

import re

import pytest

from backend.config import get_settings


@pytest.fixture
def demo_phone(monkeypatch):
    """把 `DEMO_LOGIN_PHONE` 指向指定手机号（`get_settings` 是 lru_cache 单例，必须清缓存）。"""

    def _set(phone: str) -> None:
        monkeypatch.setenv("DEMO_LOGIN_PHONE", phone)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _clear_rate_limit() -> None:
    from backend.middleware.rate_limit import _limiter

    _limiter._requests.clear()


def _parent_with_child(client, h, phone: str, name: str = "旧家长", child_name: str = "小宝"):
    parent = client.post(
        "/api/admin/members/parents", json={"name": name, "phone": phone}, headers=h
    ).json()
    child = client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": child_name},
        headers=h,
    ).json()
    return parent, child


def _mini_token(client, phone: str) -> dict:
    """家长登录（测试环境 `LOGIN_DEV_CODE=1234`）→ Authorization 头。"""
    _clear_rate_limit()
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------- B0-5：软删 / 复活必须换 token 代数 ----------------


def test_soft_deleted_parent_token_dies_immediately(client, admin_headers):
    """删档即失效：旧 token 立刻 401（`is_deleted` 守卫本来就该这样）。"""
    phone = "13800007701"
    parent, _ = _parent_with_child(client, admin_headers, phone)
    mini = _mini_token(client, phone)
    assert client.get("/api/miniapp/children", headers=mini).status_code == 200

    d = client.delete(f"/api/admin/members/parents/{parent['id']}", headers=admin_headers)
    assert d.status_code == 200, d.text
    assert client.get("/api/miniapp/children", headers=mini).status_code == 401

    # 代数已 +1（删档即撤销，不依赖 is_deleted 单一判据）
    from backend.domain.identity.models import Parent
    from tests.unit.test_wm10_concurrency import _db

    db = _db()
    try:
        row = db.query(Parent).filter(Parent.id == parent["id"]).first()
        assert row is not None
        assert int(row.token_generation or 0) >= 1
    finally:
        db.close()


def test_revival_keeps_old_token_dead(client, admin_headers):
    """B0-5 主判据：同号复活后，删档前签发的 token **仍然** 401；新登录的 token 可用。"""
    phone = "13800007702"
    parent, _ = _parent_with_child(client, admin_headers, phone, name="旧名字", child_name="小孩")
    old_mini = _mini_token(client, phone)
    assert client.get("/api/miniapp/children", headers=old_mini).status_code == 200

    assert (
        client.delete(
            f"/api/admin/members/parents/{parent['id']}", headers=admin_headers
        ).status_code
        == 200
    )

    revived = client.post(
        "/api/admin/members/parents", json={"name": "新名字", "phone": phone}, headers=admin_headers
    )
    assert revived.status_code == 200, revived.text
    assert revived.json()["id"] == parent["id"], "同号 = 同一个档案（就地复活）"

    stale = client.get("/api/miniapp/children", headers=old_mini)
    assert stale.status_code == 401, f"复活不许让旧 token 活回来：{stale.status_code} {stale.text}"

    fresh = _mini_token(client, phone)
    assert client.get("/api/miniapp/children", headers=fresh).status_code == 200


def test_revival_clears_wechat_binding(client, admin_headers, db):
    """复活不继承微信绑定：旧 openid 持有人不能靠"自动登录"接手新档案。"""
    from backend.domain.identity.models import Parent

    phone = "13800007703"
    parent, _ = _parent_with_child(client, admin_headers, phone)
    row = db.query(Parent).filter(Parent.id == parent["id"]).first()
    row.wechat_openid = "openid-b0-5-demo"
    db.commit()

    assert (
        client.delete(
            f"/api/admin/members/parents/{parent['id']}", headers=admin_headers
        ).status_code
        == 200
    )
    db.expire_all()
    row = db.query(Parent).filter(Parent.id == parent["id"]).first()
    assert row.wechat_openid is None, (
        "删档即解绑（openid 是全局唯一索引，留着还会挡住该微信绑新号）"
    )

    assert (
        client.post(
            "/api/admin/members/parents",
            json={"name": "复活名字", "phone": phone},
            headers=admin_headers,
        ).status_code
        == 200
    )
    db.expire_all()
    row = db.query(Parent).filter(Parent.id == parent["id"]).first()
    assert row.wechat_openid is None


# ---------------- B0-6：演示登录载荷脱敏 ----------------


def test_demo_payload_is_masked(client, admin_headers, demo_phone):
    """demo 载荷不得出现真实姓名/手机号/会员码/头像。"""
    phone = "13900004444"
    parent, child = _parent_with_child(
        client, admin_headers, phone, name="张伟", child_name="张小宝"
    )
    # 补个英文名（榜单展示字段）
    client.put(
        f"/api/admin/members/children/{child['id']}",
        json={"english_name": "Tommy"},
        headers=admin_headers,
    )
    demo_phone(phone)

    _clear_rate_limit()
    r = client.post("/api/miniapp/login/demo", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["demo"] is True
    assert body["parent"]["id"] == parent["id"], "id 保持真值（演示 token 靠它读 GET）"
    assert body["parent"]["phone"] == "139****4444", body["parent"]
    assert body["parent"]["name"] == "张*", body["parent"]
    assert body["parent"]["phone"] != phone

    kid = body["children"][0]
    assert kid["id"] == child["id"]
    assert kid["name"] == "张**", kid
    assert kid["english_name"] == "T****", kid
    assert kid["avatar"] is None
    code = kid["member_code"]
    assert code is not None and re.fullmatch(r"..\*{4,}", code), f"会员码必须打码：{code!r}"


def test_demo_masking_does_not_touch_stored_data(client, admin_headers, demo_phone, db):
    """脱敏只在载荷上：库里的手机号/会员码仍是真的（后台、客服要用）。"""
    from backend.domain.identity.models import Child, Parent

    phone = "13900005555"
    parent, child = _parent_with_child(client, admin_headers, phone)
    real_code = db.query(Child).filter(Child.id == child["id"]).first().member_code
    demo_phone(phone)

    _clear_rate_limit()
    assert client.post("/api/miniapp/login/demo", json={}).status_code == 200

    db.expire_all()
    row = db.query(Parent).filter(Parent.id == parent["id"]).first()
    assert row.phone == phone
    assert row.is_deleted == 0
    assert db.query(Child).filter(Child.id == child["id"]).first().member_code == real_code

    # 后台看到的仍是真手机号
    listed = client.get(
        "/api/admin/members/parents", params={"keyword": phone}, headers=admin_headers
    )
    assert listed.status_code == 200
    assert phone in listed.text
