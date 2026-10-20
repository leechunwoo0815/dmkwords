# tests/unit/test_audit8_c2_zero_coverage_20261020.py — 第八轮审查 C 批（下）：零用例高风险端点 + Quiz 并发
"""**缺陷（第八轮审查 P2-66）**：三个高风险端点**零用例**——摘掉鉴权/守卫不会被门禁发现：

| 端点 | 风险 |
|---|---|
| `GET /api/admin/activities/{id}/enrollments` | 列出某场活动的全部报名（含孩子姓名）——鉴权掉了就是**跨权限读取** |
| `GET /api/miniapp/covers/{book_id}` | 家长 token 媒体通道（query token）——鉴权掉了任何人可拉封面 |
| `GET /api/admin/circle/posts/{id}/image` | 管理端 token 媒体通道（query token）——同上 |

外加 `QuizService.submit` 的 **Child 行锁**无并发用例（测验提交要"锁孩子 → 判资格 → 落分"，
无锁时并发双提交各自按旧快照判 → 里程碑/积分重复发）。

判据都取**行为**：未带凭证必须 401；带凭证必须真的拿到内容；并发时后来者必须阻塞在锁定读上。
"""

import threading

from fastapi.testclient import TestClient

from backend.database import get_session
from tests.unit.helpers import assert_locked_read, lock_wait_query
from tests.unit.test_audit4_p2_batch2_20261017 import (
    _finish_book,
    _member_child_with_book,
    _mini_headers,
)
from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child

# ---------------------------------------------------------------- 活动报名列表


def test_admin_activity_enrollments_requires_auth_and_lists_rows(client: TestClient):
    """`GET /activities/{id}/enrollments`：① 无凭证 401；② 有权限时**真的列出报名行**。"""
    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=0, title="报名列表端点")
    c, mini = _mk_child(client, h, "13981300001", "列表孩")
    e = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    ).json()

    # ① 鉴权：无 token → 401（不是 200，也不是 500）
    anon = client.get(f"/api/admin/activities/{act['id']}/enrollments")
    assert anon.status_code == 401, f"无凭证必须 401，实 {anon.status_code} {anon.text[:120]}"

    # ② 行为：有权限时列表里必须有那一行，且带券码与孩子名
    r = client.get(f"/api/admin/activities/{act['id']}/enrollments", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1, f"应恰好 1 条报名，实 {rows}"
    row = rows[0]
    assert row["id"] == e["enrollment"]["id"]
    assert row["child_name"] == "列表孩"
    assert row["ticket_code"] == e["enrollment"]["ticket_code"]


def test_admin_activity_enrollments_unknown_activity_is_404(client: TestClient):
    """对照：不存在的活动必须 404（`_get` 抛 NotFound），不是空列表也不是 500。"""
    h = _h(client)
    r = client.get("/api/admin/activities/999999/enrollments", headers=h)
    assert r.status_code == 404, f"实 {r.status_code} {r.text[:120]}"


# ---------------------------------------------------------------- 封面媒体通道


def test_miniapp_cover_requires_parent_token(client: TestClient):
    """`GET /miniapp/covers/{book_id}`：① 无 token 401；② 有效家长 token 拿到图片字节。"""
    h = _h(client)
    c, book = _member_child_with_book(client, h, "13981300002", "封面书", 1)
    mini = _mini_headers(client, "13981300002")
    # 给书上封面（真上传，走 normalize_image）
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, "PNG")
    up = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("c.png", buf.getvalue(), "image/png")},
        headers=h,
    )
    assert up.status_code == 200, up.text

    anon = client.get(f"/api/miniapp/covers/{book['id']}")
    assert anon.status_code == 401, f"无 token 必须 401，实 {anon.status_code}"
    bad = client.get(f"/api/miniapp/covers/{book['id']}?token=not-a-token")
    assert bad.status_code == 401, f"假 token 必须 401，实 {bad.status_code}"

    ok = client.get(f"/api/miniapp/covers/{book['id']}", headers=mini)
    assert ok.status_code == 200, ok.text
    assert ok.content[:2] == b"\xff\xd8", "应返回 JPEG 字节"
    assert len(ok.content) > 100
    assert c  # 孩子在会（前置条件）——引用保留


# ---------------------------------------------------------------- 帖子卡片图


def test_circle_post_image_requires_admin_token(client: TestClient):
    """`GET /circle/posts/{id}/image`：① 无 token 401；② 有效管理端 token 拿到图片字节。

    帖子卡片图由发布链路生成（`card_render`），这里直接造一条带 image_path 的帖子，
    只验**媒体通道的鉴权与出图**（渲染本身另有用例）。
    """
    import os

    from backend.config import get_settings
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    # 造一张真图落到 uploads（媒体通道只负责把它发出去）
    rel = "circle/post-card-probe.jpg"
    root = get_settings().UPLOADS_DIR
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0" + b"\x00" * 200)
    with get_session() as db:
        post = CirclePost(
            parent_id=1,
            child_id=1,
            card_type=CirclePost.CARD_MILESTONE,
            ref_id=1,
            card_data="{}",
            image_path=rel,
            is_deleted=0,
        )
        db.add(post)
        db.commit()
        post_id = post.id

    anon = client.get(f"/api/admin/circle/posts/{post_id}/image")
    assert anon.status_code == 401, f"无 token 必须 401，实 {anon.status_code}"
    bad = client.get(f"/api/admin/circle/posts/{post_id}/image?token=not-a-token")
    assert bad.status_code == 401, f"假 token 必须 401，实 {bad.status_code}"

    ok = client.get(f"/api/admin/circle/posts/{post_id}/image", headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.content.startswith(b"\xff\xd8"), "应返回该帖子的图片字节"

    # 对照：不存在的帖子 → 404（不是 500，也不是空 200）
    missing = client.get("/api/admin/circle/posts/999999/image", headers=h)
    assert missing.status_code == 404, f"实 {missing.status_code} {missing.text[:120]}"


# ---------------------------------------------------------------- QuizService.submit 行锁


def test_quiz_submit_waits_for_child_lock(client: TestClient, session_pair):
    """`QuizService.submit` 必须**锁定读孩子行**（"锁孩子 → 判资格 → 落分"整段串行）。

    无锁时并发双提交各自按旧快照判资格 → 里程碑/积分可能重复发（同一份成绩算两次）。

    RED（去掉 `with_for_update()`）：提交的读不再阻塞，只有后面的 UPDATE 等锁 → 前缀断言红。
    """
    from backend.domain.identity.models import Child

    h = _h(client)
    phone = "13981300003"
    c, book = _member_child_with_book(client, h, phone, "并发测验书", 2)
    _finish_book(client, h, c["id"], book["id"], phone)
    mini = _mini_headers(client, phone)
    assert (
        client.get(f"/api/miniapp/quiz/{book['id']}?child_id={c['id']}", headers=mini).json()[
            "unlocked"
        ]
        is True
    )

    _s1, s2 = session_pair
    locked = (
        s2.query(Child).filter(Child.id == c["id"]).with_for_update().populate_existing().first()
    )
    assert locked is not None

    result: dict = {}

    def _submit():
        result["r"] = client.post(
            f"/api/miniapp/quiz/{book['id']}/submit",
            json={"child_id": c["id"], "answers": ["A", "A"]},
            headers=mini,
        )

    t = threading.Thread(target=_submit)
    t.start()
    try:
        assert_locked_read(lock_wait_query(), "测验提交等的 Child 锁定读")
    finally:
        s2.commit()  # 释放锁
    t.join(timeout=15)
    assert not t.is_alive(), "测验提交线程 15s 未结束（疑似死锁）"
    assert result["r"].status_code == 200, result["r"].text
    assert result["r"].json()["score"] == 2
