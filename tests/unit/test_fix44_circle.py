# tests/unit/test_fix44_circle.py — fix44 R1：阅读圈「不自赞」守卫（专家 N1）
"""N1 真缺陷：`like()` 原先只查「帖子存在 + 一孩一赞」，孩子能给自家帖点赞；
而产品语义是**不自赞**（fix33 只做了通知排除 `post.parent_id != parent.id`）。

本文件锁定三件事（真实链路，全走 HTTP）：
- 同家长拦截：**发帖孩子本人**与**同家长名下另一个孩子**都 422；
- 无副作用：被拦后 like_count 不变、CircleLike 零落库、不产生通知；
- 不放宽：**别家孩子**点赞照常 200 并落库（守卫不能误伤正常点赞）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

MILESTONE = "milestone"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent_with_child(client: TestClient, h: dict, phone: str, child_name: str, english: str):
    p = client.post(
        "/api/admin/members/parents", json={"name": "自赞家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": child_name, "english_name": english},
        headers=h,
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    mini = {
        "Authorization": (
            f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
        )
    }
    return c["id"], mini, p["id"]


def _add_sibling(client: TestClient, h: dict, parent_id: int, name: str, english: str) -> int:
    """同一家长名下再加一个孩子（跨孩拦截用——守卫按 parent_id 判，不按孩子）。"""
    c = client.post(
        f"/api/admin/members/parents/{parent_id}/children",
        json={"name": name, "english_name": english},
        headers=h,
    ).json()
    return c["id"]


def _db():
    from backend.database import get_session

    return get_session()


def _award_milestone(child_id: int, node_words: int) -> int:
    from backend.domain.growth.models import MilestoneAward

    with _db() as db:
        row = MilestoneAward(child_id=child_id, node_words=node_words, awarded_at=datetime.now())
        db.add(row)
        db.commit()
        return row.id


def _share(client: TestClient, mini: dict, child_id: int, card_type: str, ref_id: int):
    return client.post(
        "/api/miniapp/circle/posts",
        json={"child_id": child_id, "card_type": card_type, "ref_id": ref_id},
        headers=mini,
    )


def test_like_own_post_blocked_for_same_parent(client: TestClient):
    """自家帖：本人孩子 + 同家长另一个孩子都 422，且零副作用；别家孩子照常能赞。"""
    from backend.domain.reading_circle.models import CircleLike, CirclePost

    h = _h(client)
    c1, m1, p1 = _mk_parent_with_child(client, h, "13800000941", "自赞孩", "Self")
    c2 = _add_sibling(client, h, p1, "自赞孩妹", "Sibling")
    c3, m3, _ = _mk_parent_with_child(client, h, "13800000942", "外来孩", "Outsider")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]
    url = f"/api/miniapp/circle/posts/{post_id}/like"

    # ① 发帖孩子本人
    r = client.post(url, json={"child_id": c1}, headers=m1)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "不能给自己的帖子点赞"

    # ② 同家长名下**另一个**孩子（跨孩也拦——按 parent_id 判，不按 child_id）
    r = client.post(url, json={"child_id": c2}, headers=m1)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "不能给自己的帖子点赞"

    # ③ 零副作用：赞数不变、无点赞行、无被赞通知
    with _db() as db:
        assert db.query(CirclePost).filter(CirclePost.id == post_id).first().like_count == 0
        assert db.query(CircleLike).filter(CircleLike.post_id == post_id).count() == 0
    notes = client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()
    assert not [n for n in notes["items"] if n["scene"] == "circle.liked"]

    # ④ 不放宽：别家孩子点赞正常
    r = client.post(url, json={"child_id": c3}, headers=m3)
    assert r.status_code == 200, r.text
    assert r.json()["liked"] is True
    with _db() as db:
        assert db.query(CirclePost).filter(CirclePost.id == post_id).first().like_count == 1
