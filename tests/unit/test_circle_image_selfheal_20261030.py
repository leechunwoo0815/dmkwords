# tests/unit/test_circle_image_selfheal_20261030.py — 阅读圈图片缺失自愈（真实链路）
"""2026-10-29 事故回归：`uploads/circle` 属可再生目录（允许清空重生成），
但 image/thumb 端点原先只回库里的路径、**不重建缺失文件** → 清空后管理端/小程序全量 404。

判据：
- 删大图 → GET 管理端 image → 200 + image/jpeg，文件回盘、DB 路径指向真实文件；
- 删缩略图 → GET 小程序 thumb → 200，缩略图回盘；
- 快照损坏（card_data 无 card_type）→ 不渲染空卡，端点仍 404；
- 重复请求零渲染（幂等：文件在就不重建）。
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

MILESTONE = "milestone"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent(client: TestClient, h: dict, phone: str) -> int:
    return client.post(
        "/api/admin/members/parents", json={"name": "自愈家长", "phone": phone}, headers=h
    ).json()["id"]


def _mk_child(client: TestClient, h: dict, parent_id: int, name: str, english: str) -> int:
    c = client.post(
        f"/api/admin/members/parents/{parent_id}/children",
        json={"name": name, "english_name": english},
        headers=h,
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return c["id"]


def _mini(client: TestClient, phone: str) -> dict:
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin_token(client: TestClient) -> str:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    return r.json()["token"]


def _db():
    from backend.database import get_session

    return get_session()


def _award_milestone(child_id: int, node_words: int) -> int:
    from datetime import datetime

    from backend.domain.growth.models import MilestoneAward

    with _db() as db:
        row = MilestoneAward(child_id=child_id, node_words=node_words, awarded_at=datetime.now())
        db.add(row)
        db.commit()
        return row.id


def _share(client: TestClient, mini: dict, child_id: int, ref_id: int) -> int:
    r = client.post(
        "/api/miniapp/circle/posts",
        json={"child_id": child_id, "card_type": MILESTONE, "ref_id": ref_id},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    return r.json()["post_id"]


def _setup_post(client: TestClient, phone: str, child_name: str, english: str):
    h = _h(client)
    parent_id = _mk_parent(client, h, phone)
    child_id = _mk_child(client, h, parent_id, child_name, english)
    mini = _mini(client, phone)
    post_id = _share(client, mini, child_id, _award_milestone(child_id, 100000))
    return post_id, _admin_token(client), mini["Authorization"].split(" ")[1]


def _abs(rel: str) -> str:
    from backend.config import get_settings

    return os.path.abspath(os.path.join(os.path.abspath(get_settings().UPLOADS_DIR), rel))


def test_admin_image_endpoint_rebuilds_missing_card(client: TestClient):
    """删掉大图文件 → 管理端 image 端点自愈重渲（200 + image/jpeg + 文件回盘）。"""
    from backend.domain.reading_circle.models import CirclePost

    post_id, token, _ = _setup_post(client, "13800000901", "自愈孩", "Heal")
    with _db() as db:
        before = db.query(CirclePost).filter(CirclePost.id == post_id).first().image_path
    full = _abs(before)
    assert os.path.isfile(full)
    os.remove(full)  # 模拟「为修字体清空 uploads/circle」

    r = client.get(f"/api/admin/circle/posts/{post_id}/image?token={token}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert len(r.content) > 1024

    with _db() as db:
        after = db.query(CirclePost).filter(CirclePost.id == post_id).first().image_path
    assert os.path.isfile(_abs(after))  # 路径指向真实文件
    # 纸纹噪点使每次渲染字节不同 ⇒ 内容寻址必然换名（对端上是好事：破旧图缓存）
    assert after != before

    # 幂等：文件已在 → 再请求不重渲（路径不再变、响应仍 200）
    r2 = client.get(f"/api/admin/circle/posts/{post_id}/image?token={token}")
    assert r2.status_code == 200 and r2.content == r.content
    with _db() as db:
        again = db.query(CirclePost).filter(CirclePost.id == post_id).first().image_path
    assert again == after


def test_miniapp_thumb_endpoint_rebuilds_missing_thumb(client: TestClient):
    """删掉缩略图文件 → 小程序 thumb 端点自愈重渲（信息流小图不再 404）。"""
    from backend.domain.reading_circle.models import CirclePost

    post_id, _, mini_token = _setup_post(client, "13800000902", "缩略自愈", "ThumbHeal")
    with _db() as db:
        thumb = db.query(CirclePost).filter(CirclePost.id == post_id).first().thumb_path
    assert thumb
    os.remove(_abs(thumb))

    r = client.get(f"/api/miniapp/circle/posts/{post_id}/thumb?token={mini_token}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")

    with _db() as db:
        after = db.query(CirclePost).filter(CirclePost.id == post_id).first().thumb_path
    assert os.path.isfile(_abs(after))


def test_self_heal_skips_corrupt_card_data(client: TestClient):
    """card_data 快照损坏（无 card_type）→ 不渲染空卡、不 500，端点如实 404。"""
    from backend.domain.reading_circle.models import CirclePost

    post_id, token, _ = _setup_post(client, "13800000903", "坏快照", "Broken")
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        os.remove(_abs(post.image_path))
        post.card_data = "{}"
        db.commit()

    r = client.get(f"/api/admin/circle/posts/{post_id}/image?token={token}")
    assert r.status_code == 404, r.text
    assert "卡片图文件不存在" in r.text
