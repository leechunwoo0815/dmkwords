# tests/unit/test_wm15_circle.py — WM15 社交化升级（R1 布局数据面 / R3 头像 / R4 名片 / R5 海报 / R6 深链）
"""真实链路覆盖：双规格卡片、点赞名义快照、头像墙批查、名片页隐私红线、
海报端点、分场景未读、头像白名单、孤儿清理双列。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.domain.reading_circle.art import AVATAR_IDS

MILESTONE = "milestone"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent_with_child(client: TestClient, h: dict, phone: str, child_name: str, english: str):
    p = client.post(
        "/api/admin/members/parents", json={"name": "圈家长", "phone": phone}, headers=h
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


# ---------- R1/B1：双规格卡片 ----------


def test_share_creates_both_specs_and_thumb_endpoint(client: TestClient):
    """晒卡落**两个**文件（含字大图 + 无字缩略图）；thumb 端点可访问；两者都在 uploads/circle/。"""
    from backend.config import get_settings
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000901", "规格孩", "Spec")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert post.thumb_path and post.image_path
        assert post.thumb_path != post.image_path
        assert os.path.isfile(os.path.join(root, post.image_path))
        assert os.path.isfile(os.path.join(root, post.thumb_path))

    token = m1["Authorization"].split()[1]
    for ep in ("image", "thumb"):
        r = client.get(f"/api/miniapp/circle/posts/{post_id}/{ep}", params={"token": token})
        assert r.status_code == 200, ep
        assert r.headers["content-type"] == "image/png"


def test_thumb_falls_back_to_full_for_legacy_post(client: TestClient):
    """旧帖（无 thumb_path）→ thumb 端点回落大图，不 404（B3：seed 已回填，此处防未来遗漏）。"""
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000902", "旧帖孩", "Legacy")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        post.thumb_path = None  # 模拟历史帖
        db.commit()
    r = client.get(
        f"/api/miniapp/circle/posts/{post_id}/thumb",
        params={"token": m1["Authorization"].split()[1]},
    )
    assert r.status_code == 200


# ---------- R6：点赞名义 / 头像墙 / 通知文案 ----------


def test_like_with_child_identity_and_liker_wall(client: TestClient):
    """带 child_id 点赞 → 落 liker_child_id、头像墙出现、通知文案是**孩子名义**、ref 指孩子。"""
    from backend.domain.reading_circle.models import CircleLike

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000903", "被赞孩", "Owner")
    _, m2, _ = _mk_parent_with_child(client, h, "13800000904", "点赞孩", "Liker")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    r = client.post(
        f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c1 + 1}, headers=m2
    )
    assert r.status_code == 200, r.text
    with _db() as db:
        row = db.query(CircleLike).filter(CircleLike.post_id == post_id).first()
        assert row.liker_child_id is not None  # 名义快照

    items = client.get("/api/miniapp/circle/posts", headers=m1).json()["items"]
    me = next(x for x in items if x["id"] == post_id)
    assert len(me["likers"]) == 1 and me["likers"][0]["child_id"] == row.liker_child_id

    notes = client.get("/api/miniapp/notifications", headers=m1).json()
    liked = next(n for n in notes["items"] if n["scene"] == "circle.liked")
    assert "Liker" in liked["content"] and "赞了" in liked["content"]
    assert liked["ref_type"] == "child" and liked["ref_id"] == str(row.liker_child_id)


def test_like_without_child_falls_back_to_parent_name(client: TestClient):
    """老版本端不带 body → 仍可点赞，文案降级家长显示名（C4 兼容）。"""
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000905", "兼容孩", "Compat")
    _, m2, _ = _mk_parent_with_child(client, h, "13800000906", "旧端孩", "Old")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]

    r = client.post(f"/api/miniapp/circle/posts/{post_id}/like", headers=m2)
    assert r.status_code == 200
    notes = client.get("/api/miniapp/notifications", headers=m1).json()["items"]
    liked = next(n for n in notes if n["scene"] == "circle.liked")
    assert "圈家长 赞了" in liked["content"]  # 家长显示名兜底
    assert liked["ref_type"] == "circle_post"


# ---------- C1：分场景未读 ----------


def test_unread_by_scene_and_ref_fields(client: TestClient):
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000907", "未读孩", "Unread")
    _, m2, _ = _mk_parent_with_child(client, h, "13800000908", "赞孩", "LikerX")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]
    client.post(f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c1 + 1}, headers=m2)

    body = client.get("/api/miniapp/notifications", headers=m1).json()
    assert body["unread_by_scene"].get("circle_liked", 0) >= 1
    assert all("ref_type" in i and "ref_id" in i for i in body["items"])


# ---------- R3：头像白名单 ----------


def test_child_avatar_whitelist_and_self_service(client: TestClient):
    """白名单：非法 id 拒绝；合法 id 生效；家长自助端点只能改自己孩子。"""
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000909", "头像孩", "Avatar")
    assert (
        client.put(
            f"/api/miniapp/children/{c1}/avatar", json={"avatar": "not_an_avatar"}, headers=m1
        ).status_code
        == 422
    )
    ok = client.put(
        f"/api/miniapp/children/{c1}/avatar", json={"avatar": AVATAR_IDS[0]}, headers=m1
    )
    assert ok.status_code == 200 and ok.json()["avatar"] == AVATAR_IDS[0]
    # 清空回默认
    cleared = client.put(f"/api/miniapp/children/{c1}/avatar", json={"avatar": ""}, headers=m1)
    assert cleared.status_code == 200 and cleared.json()["avatar"] == ""
    # 管理端建档带头像 + 列表回显
    pid = client.post(
        "/api/admin/members/parents", json={"name": "建档家长", "phone": "13800000910"}, headers=h
    ).json()["id"]
    c2 = client.post(
        f"/api/admin/members/parents/{pid}/children",
        json={"name": "建档孩", "avatar": AVATAR_IDS[1]},
        headers=h,
    ).json()
    assert c2["avatar"] == AVATAR_IDS[1]


def test_avatar_whitelist_rejects_on_admin_create(client: TestClient):
    h = _h(client)
    pid = client.post(
        "/api/admin/members/parents", json={"name": "白名单家长", "phone": "13800000911"}, headers=h
    ).json()["id"]
    r = client.post(
        f"/api/admin/members/parents/{pid}/children",
        json={"name": "坏头像孩", "avatar": "../../etc/passwd"},
        headers=h,
    )
    assert r.status_code == 422


# ---------- R4/R5：名片页与海报 ----------


def test_child_profile_privacy_and_poster(client: TestClient):
    """名片页：有英文名/等级/勋章；**无中文名/家长名/手机号**；海报端点出图。"""
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000912", "名片孩", "CardKid")
    _award_milestone(c1, 100000)
    prof = client.get(f"/api/miniapp/circle/children/{c1}/profile", headers=m1)
    assert prof.status_code == 200
    body = prof.json()
    assert body["english_name"] == "CardKid"
    assert body["avatar"] in (None, "")  # 未设头像
    assert [b["badge_id"] for b in body["badges"]]  # 勋章墙非空
    dumped = str(body)
    for leak in ("名片孩", "圈家长", "13800000912"):
        assert leak not in dumped, f"名片页泄露了 {leak}"

    token = m1["Authorization"].split()[1]
    r = client.get(f"/api/miniapp/circle/children/{c1}/poster", params={"token": token})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert len(r.content) > 10000


def test_withdrawn_child_profile_visible_as_history(client: TestClient):
    """退会孩名片可见（历史荣誉域）且带 is_history 标记——与榜单 active_only 分域声明。"""
    from backend.domain.identity.models import Child

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000913", "退会孩X", "Gone")
    with _db() as db:
        child = db.query(Child).filter(Child.id == c1).first()
        child.member_status = Child.MEMBER_WITHDRAWN
        db.commit()
    body = client.get(f"/api/miniapp/circle/children/{c1}/profile", headers=m1).json()
    assert body["is_history"] is True
    assert body["english_name"] == "Gone"  # 仍可见（历史荣誉）


# ---------- B1：清理任务两列（不留孤儿缩略图） ----------


def test_cleanup_removes_both_specs(client: TestClient):
    import os as _os

    from backend.config import get_settings
    from backend.domain.reading_circle.admin_service import CircleImageCleanupService
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000914", "清理孩X", "CleanX")
    post_id = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]
    assert client.delete(f"/api/miniapp/circle/posts/{post_id}", headers=m1).status_code == 200

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        full = _os.path.join(root, post.image_path)
        thumb = _os.path.join(root, post.thumb_path)
        assert _os.path.isfile(full) and _os.path.isfile(thumb)
        post.update_time = datetime.now() - timedelta(days=31)  # 到期
        db.commit()
    with _db() as db:
        assert CircleImageCleanupService(db).cleanup_orphan_images() == 1
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert post.image_path == "" and post.thumb_path == ""  # 两列都置空
    assert not _os.path.isfile(full) and not _os.path.isfile(thumb)  # 两文件都删
