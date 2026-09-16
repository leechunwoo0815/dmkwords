# tests/unit/test_miniapp_children_refresh.py — 小程序孩子列表刷新（2026-09-16）
"""「点赞的头像跟我的页面的头像不匹配」防复发测试。

根因：小程序把**登录那一刻**的孩子列表缓存在本地（`session.getChildren()`），而点赞墙/
阅读圈/排行榜走服务端实时数据——店主在后台改了孩子头像（或会员到期）后本地快照永不同步。
修法 = 新增 `GET /api/miniapp/children`（与登录载荷**同源**：`identity.auth.children_payload`）
供小程序在前台/我的页刷新缓存。

本测试锁死「刷新链路看得见后台改动」这条前提：
  ① 端点与登录载荷字段集合完全一致（防两个消费方各写一份、字段漂移）；
  ② 管理员改头像后端点返回**新值**；
  ③ 载荷含 member_expire/member_start（我的页「到期时间」行依赖；旧登录载荷缺这两个字段，
     正式会员的到期行永远不显示）；
  ④ 端点只返回**本人**的孩子（越权红线）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(
    client: TestClient, h: dict, phone: str, child_name: str = "刷新孩"
) -> tuple[dict, dict]:
    p = client.post(
        "/api/admin/members/parents", json={"name": "刷新家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    return p, c


def _mini_login(client: TestClient, phone: str) -> dict:
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    assert r.status_code == 200, r.text
    return r.json()


def test_children_refresh_sees_admin_change(client: TestClient):
    """端点与登录同源；后台改头像后刷新端点必须返回新值（这是刷新能修好问题的前提）。"""
    h = _h(client)
    _p, c = _family(client, h, "13800000831")
    login = _mini_login(client, "13800000831")
    tok = {"Authorization": f"Bearer {login['token']}"}

    r = client.get("/api/miniapp/children", headers=tok)
    assert r.status_code == 200, r.text
    children = r.json()["children"]
    assert [x["id"] for x in children] == [c["id"]]

    # ① 同源：字段集合与登录载荷一致
    assert set(children[0].keys()) == set(login["children"][0].keys())
    # ③ 到期字段在位（我的页「到期时间」行依赖）
    assert "member_expire" in children[0] and "member_start" in children[0]

    # ② 管理员改头像（真实入口）→ 刷新端点看到新值
    upd = client.put(
        f"/api/admin/members/children/{c['id']}", json={"avatar": "penguin_mint"}, headers=h
    )
    assert upd.status_code == 200, upd.text
    after = client.get("/api/miniapp/children", headers=tok).json()["children"]
    assert after[0]["avatar"] == "penguin_mint", after


def test_children_endpoint_is_parent_scoped(client: TestClient):
    """越权红线：只返回本人孩子，拿不到别人家的（2026-09-16 新增端点必测）。"""
    h = _h(client)
    _p1, c1 = _family(client, h, "13800000832", "我的孩")
    _p2, c2 = _family(client, h, "13800000833", "别人孩")
    mine = _mini_login(client, "13800000832")
    got = client.get(
        "/api/miniapp/children", headers={"Authorization": f"Bearer {mine['token']}"}
    ).json()["children"]
    assert [x["id"] for x in got] == [c1["id"]]
    assert c2["id"] not in {x["id"] for x in got}


def test_login_payload_carries_member_expire(client: TestClient):
    """登录载荷本身也要带 member_expire —— 我的页冷启动（还没刷新）时读的就是它。"""
    h = _h(client)
    p, c = _family(client, h, "13800000834", "冷启孩")
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    login = _mini_login(client, "13800000834")
    me = login["children"][0]
    assert me["id"] == c["id"]
    assert me["member_expire"], me  # 观察期收款后必有到期日
    assert me["member_start"], me
    assert login["parent"]["id"] == p["id"]
