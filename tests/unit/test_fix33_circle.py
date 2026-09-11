# tests/unit/test_fix33_circle.py — fix33 阅读圈点赞主体切换 + 缩略图同构图（真实链路）
"""覆盖专家 fix33 要求的测试增量：
- R2 兄弟同赞一帖 → 两行（唯一索引 (post_id, child_id) 生效）；
- R2 切孩 liked_by_me 隔离 + 取消点赞按孩子定位；
- R2 缺 child_id 点赞/取消 → 422「请先选择孩子」（含整包 body 不带的旧端姿势）；
- R2 迁移历史数据：child_id 回填 + 无孩老赞扣减计数后软删（幂等）；
- R1 缩略图规格：落盘带当前规格版本号；旧规格重渲且删旧文件，重跑零渲染。
"""

from __future__ import annotations

import importlib.util
import os
import shutil

from fastapi.testclient import TestClient

MILESTONE = "milestone"
MIGRATION_FILE = "c3a9e5b1d7f2_fix33_circle_like_child_subject.py"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent(client: TestClient, h: dict, phone: str) -> int:
    return client.post(
        "/api/admin/members/parents", json={"name": "圈家长", "phone": phone}, headers=h
    ).json()["id"]


def _mk_child(client: TestClient, h: dict, parent_id: int, name: str, english: str) -> int:
    """建孩子并走真链收款（观察期会员 → 可晒卡）。"""
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


def _feed(client: TestClient, mini: dict, target_post_id: int, child_id: int | None) -> dict:
    """取信息流里目标帖的视图（child_id=None → 不传参数 = 未选孩子的浏览态）。"""
    qs = f"?child_id={child_id}" if child_id else ""
    items = client.get(f"/api/miniapp/circle/posts{qs}", headers=mini).json()["items"]
    return next(p for p in items if p["id"] == target_post_id)


# ---------- R2：兄弟同赞一帖（唯一索引切到孩子） ----------


def test_siblings_both_like_same_post(client: TestClient):
    """同一家长的两个孩子各赞同一帖 → 两行、计数 2、头像墙两个（旧口径 (post,parent) 会互斥）。"""
    from backend.domain.reading_circle.models import CircleLike

    h = _h(client)
    owner_p = _mk_parent(client, h, "13800000801")
    owner_c = _mk_child(client, h, owner_p, "帖主孩", "Owner")
    owner_mini = _mini(client, "13800000801")
    post_id = _share(client, owner_mini, owner_c, _award_milestone(owner_c, 100000))

    fan_p = _mk_parent(client, h, "13800000802")
    a = _mk_child(client, h, fan_p, "哥哥", "Tommy")
    b = _mk_child(client, h, fan_p, "妹妹", "Lisa")
    fan_mini = _mini(client, "13800000802")

    for cid in (a, b):
        r = client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": cid}, headers=fan_mini
        )
        assert r.status_code == 200, r.text

    with _db() as db:
        rows = db.query(CircleLike).filter(CircleLike.post_id == post_id).all()
        assert {row.child_id for row in rows} == {a, b}
        assert len(rows) == 2

    view = _feed(client, owner_mini, post_id, a)
    assert view["like_count"] == 2
    assert {lk["child_id"] for lk in view["likers"]} == {a, b}


# ---------- R2：切孩 liked_by_me 隔离 + 取消按孩子定位 ----------


def test_liked_by_me_isolated_per_child(client: TestClient):
    h = _h(client)
    owner_p = _mk_parent(client, h, "13800000803")
    owner_c = _mk_child(client, h, owner_p, "被赞孩", "Owner2")
    owner_mini = _mini(client, "13800000803")
    post_id = _share(client, owner_mini, owner_c, _award_milestone(owner_c, 100000))

    fan_p = _mk_parent(client, h, "13800000804")
    a = _mk_child(client, h, fan_p, "哥哥", "Tommy2")
    b = _mk_child(client, h, fan_p, "妹妹", "Lisa2")
    fan_mini = _mini(client, "13800000804")

    client.post(f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": a}, headers=fan_mini)
    # 选中 A → 已赞；切到 B → 未赞；未选孩子 → 浏览态未赞（同一家长三种上下文互不串味）
    assert _feed(client, fan_mini, post_id, a)["liked_by_me"] is True
    assert _feed(client, fan_mini, post_id, b)["liked_by_me"] is False
    assert _feed(client, fan_mini, post_id, None)["liked_by_me"] is False

    # 用 B 取消 → 幂等空操作（不能顶掉 A 的赞）
    r = client.delete(
        f"/api/miniapp/circle/posts/{post_id}/like", params={"child_id": b}, headers=fan_mini
    )
    assert r.status_code == 200 and r.json()["liked"] is False
    assert _feed(client, fan_mini, post_id, a)["liked_by_me"] is True
    assert _feed(client, fan_mini, post_id, a)["like_count"] == 1

    # 用 A 取消 → 计数归零 + 头像墙清空（R3 的服务端口径）
    r = client.delete(
        f"/api/miniapp/circle/posts/{post_id}/like", params={"child_id": a}, headers=fan_mini
    )
    assert r.status_code == 200 and r.json()["like_count"] == 0
    view = _feed(client, fan_mini, post_id, a)
    assert view["liked_by_me"] is False and view["likers"] == []


# ---------- R2：缺 child_id 的 422 文案 ----------


def test_like_and_unlike_require_child(client: TestClient):
    h = _h(client)
    owner_p = _mk_parent(client, h, "13800000805")
    owner_c = _mk_child(client, h, owner_p, "必填孩", "Owner3")
    owner_mini = _mini(client, "13800000805")
    post_id = _share(client, owner_mini, owner_c, _award_milestone(owner_c, 100000))

    fan_p = _mk_parent(client, h, "13800000806")
    _mk_child(client, h, fan_p, "老端孩", "OldClient")
    fan_mini = _mini(client, "13800000806")

    url = f"/api/miniapp/circle/posts/{post_id}/like"
    for body in (None, {"child_id": None}):
        r = client.post(url, json=body, headers=fan_mini)
        assert r.status_code == 422, r.text
        assert "请先选择孩子" in str(r.json()), r.text
    r = client.delete(url, headers=fan_mini)  # DELETE 不带 child_id
    assert r.status_code == 422 and "请先选择孩子" in str(r.json()), r.text


# ---------- R2：迁移历史数据处理（回填 + 软删 + 扣减，幂等） ----------


def _migration_module():
    """按文件路径加载迁移模块。

    不能写 `import alembic.versions...`——`alembic` 是已安装的第三方包名，会遮蔽
    仓库内的同名目录；用 spec_from_file_location 显式取本仓文件。
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / MIGRATION_FILE
    spec = importlib.util.spec_from_file_location("fix33_like_migration", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_backfills_child_and_softdeletes_legacy_likes(client: TestClient):
    """存量行：有名义 → child_id 回填；无孩老赞 → 扣减 like_count 后软删；重跑幂等。"""
    from backend.database import engine
    from backend.domain.identity.models import Child
    from backend.domain.reading_circle.models import CircleLike, CirclePost

    h = _h(client)
    owner_p = _mk_parent(client, h, "13800000807")
    owner_c = _mk_child(client, h, owner_p, "迁移孩", "Mig")
    owner_mini = _mini(client, "13800000807")
    post_id = _share(client, owner_mini, owner_c, _award_milestone(owner_c, 100000))

    with _db() as db:
        owner_pid = db.query(Child).filter(Child.id == owner_c).first().parent_id
        # ① 有名义的存量行（WM15 老版本：只落 liker_child_id，无 child_id 列）
        db.add(
            CircleLike(post_id=post_id, parent_id=owner_pid, child_id=None, liker_child_id=owner_c)
        )
        # ② 无孩历史赞（WM15 前老端只有 parent_id）→ 迁移应扣减计数并软删
        db.add(CircleLike(post_id=post_id, parent_id=owner_pid, child_id=None, liker_child_id=None))
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        post.like_count = 2  # 两条活跃行
        db.commit()

    mod = _migration_module()
    for _ in range(2):  # 第二次 = 幂等验证（不再扣减、不再改行）
        with engine.begin() as conn:
            mod._migrate_like_subject(conn)

    with _db() as db:
        rows = (
            db.query(CircleLike)
            .filter(CircleLike.post_id == post_id)
            .order_by(CircleLike.id.asc())
            .all()
        )
        named = next(r for r in rows if r.liker_child_id)
        legacy = next(r for r in rows if r.liker_child_id is None)
        assert named.child_id == owner_c and named.is_deleted == 0  # 回填且仍活跃
        assert legacy.is_deleted == 1 and legacy.child_id is None  # 无孩赞软删、child_id 留空
        assert db.query(CirclePost).filter(CirclePost.id == post_id).first().like_count == 1


# ---------- R1：缩略图规格同构图 ----------


def test_thumb_spec_stamped_and_rerender_is_idempotent(client: TestClient):
    """缩略图落盘带当前规格版本号；旧规格重渲并删旧文件；重跑零渲染（幂等）。"""
    from backend.config import get_settings
    from backend.domain.reading_circle.card_engine import THUMB_SPEC_VERSION, ensure_circle_thumbs
    from backend.domain.reading_circle.models import CirclePost

    marker = f"thumb_{THUMB_SPEC_VERSION}_"
    h = _h(client)
    p = _mk_parent(client, h, "13800000808")
    c = _mk_child(client, h, p, "缩略孩", "Thumb")
    mini = _mini(client, "13800000808")
    post_id = _share(client, mini, c, _award_milestone(c, 100000))

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert post.image_path and post.thumb_path
        assert marker in post.thumb_path  # 新帖即当前规格
        # 伪造「旧规格缩略图」：同文件换成旧命名（seed 重渲前的现场状态）
        legacy_rel = post.thumb_path.replace(marker, "thumb_")
        shutil.copyfile(os.path.join(root, post.thumb_path), os.path.join(root, legacy_rel))
        post.thumb_path = legacy_rel
        db.commit()

    with _db() as db:
        res = ensure_circle_thumbs(db)
        assert res == {"rendered": 1, "skipped": 0}
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert marker in post.thumb_path and post.thumb_path != legacy_rel
    assert not os.path.isfile(os.path.join(root, legacy_rel))  # 旧文件已删，不留无主残留

    with _db() as db:  # 幂等：已是当前规格 → 零渲染
        assert ensure_circle_thumbs(db)["rendered"] == 0
