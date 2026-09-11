# tests/unit/test_wm14_circle.py — WM14-A 阅读圈（真实链路）
"""FEAT-084 一期 MVP：晒卡（归属/终身唯一/日限2）+ 点赞（一心一赞/原子计数）
+ 馆长赞（特殊文案通知）+ 删除（家长自己/超管必填原因）+ 置顶互斥/分页/未赞数。"""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

# 卡片类型常量（与后端 CARD_TYPES 对齐）
MILESTONE = "milestone"
PERFECT_QUIZ = "perfect_quiz"
STREAK = "streak"
FINISH_BOOK = "finish_book"


def _h(client: TestClient, username: str = "admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_parent_with_child(client: TestClient, h: dict, phone: str, child_name: str, english: str):
    """建家长+孩子（会员走真链收款），返回 (child_id, mini_headers)。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "圈家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": child_name, "english_name": english},
        headers=h,
    ).json()
    o = client.post(
        "/api/admin/orders",
        json={"child_id": c["id"], "order_type": "observation_fee"},
        headers=h,
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return c["id"], mini


def _seed_book(client: TestClient, h: dict, isbn: str, words: int) -> int:
    b = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": f"Circle{isbn[-3:]}", "word_count": words},
        headers=h,
    ).json()
    return b["id"]


def _db():
    from backend.database import get_session

    return get_session()


def _award_milestone(child_id: int, node_words: int) -> int:
    """直插里程碑达成记录（造数先例同 _credit_words：WordsLedger 直插）。"""
    from backend.domain.growth.models import MilestoneAward

    with _db() as db:
        row = MilestoneAward(child_id=child_id, node_words=node_words, awarded_at=datetime.now())
        db.add(row)
        db.commit()
        return row.id


def _award_perfect_quiz(child_id: int, book_id: int) -> int:
    """直插满分测验提交（快照保真口径：5/5）。"""
    from backend.domain.growth.models import QuizAttempt

    with _db() as db:
        row = QuizAttempt(
            child_id=child_id,
            book_id=book_id,
            score=5,
            total_questions=5,
            passed=1,
            submitted_at=datetime.now(),
        )
        db.add(row)
        db.commit()
        return row.id


def _award_streak(child_id: int, cycle_type: str, streak: int) -> int:
    from backend.domain.growth.models import CheckinStreakRecord

    with _db() as db:
        row = CheckinStreakRecord(
            child_id=child_id,
            cycle_type=cycle_type,
            cycle_no=1,
            streak_at=streak,
            awarded_at=datetime.now(),
        )
        db.add(row)
        db.commit()
        return row.id


def _credit_words(child_id: int, book_id: int, words: int) -> int:
    from backend.domain.growth.models import WordsLedger

    with _db() as db:
        row = WordsLedger(
            child_id=child_id, book_id=book_id, word_count=words, created_at=datetime.now()
        )
        db.add(row)
        db.commit()
        return row.id


def _share(client: TestClient, mini: dict, child_id: int, card_type: str, ref_id: int):
    return client.post(
        "/api/miniapp/circle/posts",
        json={"child_id": child_id, "card_type": card_type, "ref_id": ref_id},
        headers=mini,
    )


# ---------- 1) 权限红线：只能晒自己孩子的成就 ----------


def test_share_other_child_achievement_rejected(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000701", "别家孩", "Other")
    c2, m2 = _mk_parent_with_child(client, h, "13800000702", "自家孩", "Mine")
    ms = _award_milestone(c1, 100000)
    # 家长2 伪造 ref_id 晒家长1 孩子的成就 → 422 + 不落帖
    r = _share(client, m2, c2, MILESTONE, ms)
    assert r.status_code == 422, f"晒他人成就应 422: {r.status_code} {r.text[:120]}"
    posts = client.get("/api/miniapp/circle/posts", headers=m1).json()
    assert posts["total"] == 0


# ---------- 2) 同一成就终身只可晒一次 ----------


def test_same_achievement_only_once(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000703", "唯一孩", "Once")
    ms = _award_milestone(c1, 500000)
    r = _share(client, m1, c1, MILESTONE, ms)
    assert r.status_code == 200, r.text
    r2 = _share(client, m1, c1, MILESTONE, ms)
    assert r2.status_code == 422
    assert "已晒过" in r2.json()["detail"]
    # 已晒成就出现在 my-cards 的 shared 分组（不再出现在 available）
    cards = client.get(f"/api/miniapp/circle/my-cards?child_id={c1}", headers=m1).json()
    shared_refs = [c["ref_id"] for c in cards["shared"]]
    assert ms in shared_refs


# ---------- 3) 每日限晒 2 帖 ----------


def test_daily_limit_two_posts(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000704", "日限孩", "Limit")
    b1 = _seed_book(client, h, "9788400000001", 300)
    b2 = _seed_book(client, h, "9788400000002", 400)
    b3 = _seed_book(client, h, "9788400000003", 500)
    _credit_words(c1, b1, 300)
    _credit_words(c1, b2, 400)
    _credit_words(c1, b3, 500)
    # Q10 口径：完读卡 ref_id = book_id（不是账目行 id）
    assert _share(client, m1, c1, FINISH_BOOK, b1).status_code == 200
    assert _share(client, m1, c1, FINISH_BOOK, b2).status_code == 200
    # 第 3 帖 → 422
    r3 = _share(client, m1, c1, FINISH_BOOK, b3)
    assert r3.status_code == 422
    assert "2" in r3.json()["detail"]  # 文案含上限值（配置化）
    posts = client.get("/api/miniapp/circle/posts", headers=m1).json()
    assert posts["total"] == 2


# ---------- 4) 一心一赞：唯一 + 取消 + 再赞计数 ----------


def test_like_unique_cancel_relike(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000705", "帖主孩", "Poster")
    c2, m2 = _mk_parent_with_child(client, h, "13800000706", "点赞孩", "Liker")
    ms = _award_milestone(c1, 100000)
    post_id = _share(client, m1, c1, MILESTONE, ms).json()["post_id"]
    feed = f"/api/miniapp/circle/posts?child_id={c2}"  # fix33：liked_by_me 按孩子算

    def _like():  # 小程序端家长2 的孩子 c2 的赞
        return client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        )

    r = _like()
    assert r.status_code == 200, r.text
    items = client.get(feed, headers=m2).json()["items"]
    post = next(p for p in items if p["id"] == post_id)
    assert post["like_count"] == 1 and post["liked_by_me"] is True
    # 重复点赞：幂等返回但计数仍 1（库级唯一约束兜底）
    r2 = _like()
    assert r2.status_code == 200
    post = next(p for p in client.get(feed, headers=m2).json()["items"] if p["id"] == post_id)
    assert post["like_count"] == 1
    # 取消 → 0；再赞 → 1（计数经原子 UPDATE 保持一致）
    assert (
        client.delete(
            f"/api/miniapp/circle/posts/{post_id}/like?child_id={c2}", headers=m2
        ).status_code
        == 200
    )
    post = next(p for p in client.get(feed, headers=m2).json()["items"] if p["id"] == post_id)
    assert post["like_count"] == 0 and post["liked_by_me"] is False
    assert _like().status_code == 200
    post = next(p for p in client.get(feed, headers=m2).json()["items"] if p["id"] == post_id)
    assert post["like_count"] == 1


# ---------- 5) 馆长赞特殊文案 + 被赞通知落库 ----------


def test_admin_like_and_notifications(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000707", "被赞孩", "Liked")
    c2, m2 = _mk_parent_with_child(client, h, "13800000708", "点赞孩", "Fan")
    b1 = _seed_book(client, h, "9788400000011", 600)
    qa = _award_perfect_quiz(c1, b1)
    post_id = _share(client, m1, c1, PERFECT_QUIZ, qa).json()["post_id"]

    # 普通孩子赞 → 帖主收「Fan 赞了 Liked 的成就」（fix33：名义=孩子，非家长显示名）
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    notes = client.get("/api/miniapp/notifications?category=其他", headers=m1).json()["items"]
    assert any("Fan 赞了 Liked 的成就" in n["content"] for n in notes), notes

    # 馆长行内赞 → admin_liked 标记 + like_count+1 + 特殊文案「馆长赞了 ...」
    r = client.post(f"/api/admin/circle/posts/{post_id}/admin-like", headers=h)
    assert r.status_code == 200, r.text
    items = client.get("/api/miniapp/circle/posts", headers=m1).json()["items"]
    post = next(p for p in items if p["id"] == post_id)
    assert post["admin_liked"] is True and post["like_count"] == 2
    notes = client.get("/api/miniapp/notifications?category=其他", headers=m1).json()["items"]
    assert any("馆长赞了 Liked 的成就" in n["content"] for n in notes), notes
    # 取消馆长赞：标记清零、计数回落
    assert (
        client.delete(f"/api/admin/circle/posts/{post_id}/admin-like", headers=h).status_code == 200
    )
    post = next(
        p
        for p in client.get("/api/miniapp/circle/posts", headers=m1).json()["items"]
        if p["id"] == post_id
    )
    assert post["admin_liked"] is False and post["like_count"] == 1


# ---------- 6) 删除权限：家长自己 / 他人 422 / 超管必填原因 ----------


def test_delete_post_permissions(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000709", "删帖孩", "Deleter")
    _, m2 = _mk_parent_with_child(client, h, "13800000710", "他删孩", "Outsider")
    ms = _award_milestone(c1, 1000000)
    post_id = _share(client, m1, c1, MILESTONE, ms).json()["post_id"]
    # 他人家长删 → 422
    r = client.delete(f"/api/miniapp/circle/posts/{post_id}", headers=m2)
    assert r.status_code == 422
    # 家长删自己帖 → 200 且从信息流消失（Q15：DELETE 无 body）
    r = client.delete(f"/api/miniapp/circle/posts/{post_id}", headers=m1)
    assert r.status_code == 200, r.text
    assert client.get("/api/miniapp/circle/posts", headers=m1).json()["total"] == 0
    # 超管删帖：必填原因（留痕）
    ms2 = _award_milestone(c1, 5000000)
    post_id2 = _share(client, m1, c1, MILESTONE, ms2).json()["post_id"]
    r = client.request(
        "DELETE", f"/api/admin/circle/posts/{post_id2}", json={"reason": ""}, headers=h
    )
    assert r.status_code == 422
    r = client.request(
        "DELETE",
        f"/api/admin/circle/posts/{post_id2}",
        json={"reason": "测试删除留痕"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/miniapp/circle/posts", headers=m1).json()["total"] == 0


# ---------- 7) 置顶互斥 + 分页置顶置首 + 未赞数统计 ----------


def test_pin_exclusive_pagination_unliked_count(client: TestClient):
    h = _h(client)
    # 造 4 帖：日限按 child 口径——1 个家长 4 个孩子各晒 1 帖（同一家长复用 1 次
    # 小程序登录，规避 /login 限流 5 次/60s 误伤测试）
    p = client.post(
        "/api/admin/members/parents", json={"name": "流家长", "phone": "13800000721"}, headers=h
    ).json()
    mini = {
        "Authorization": (
            f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000721', 'code': '1234'}).json()['token']}"
        )
    }
    post_ids = []
    for i in range(4):
        c = client.post(
            f"/api/admin/members/parents/{p['id']}/children",
            json={"name": f"流孩{i}", "english_name": f"Feed{i}"},
            headers=h,
        ).json()
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
        ms = _award_milestone(c["id"], 100000 * (i + 1))
        post_ids.append(_share(client, mini, c["id"], MILESTONE, ms).json()["post_id"])
    # 未赞数：今日新帖 4 条全部未馆长赞
    unliked = client.get("/api/admin/circle/unliked-count", headers=h).json()
    assert unliked["count"] == 4
    # 置顶第 3 帖 → 小程序列表第一条是它
    r = client.post(f"/api/admin/circle/posts/{post_ids[2]}/pin", headers=h)
    assert r.status_code == 200, r.text
    items = client.get("/api/miniapp/circle/posts?page=1&page_size=2", headers=mini).json()["items"]
    assert items[0]["id"] == post_ids[2] and items[0]["is_pinned"] is True
    assert items[1]["id"] == post_ids[3]  # 时间倒序：最新帖随后
    # 置顶互斥：置顶第 1 帖后，第 3 帖自动取消
    r = client.post(f"/api/admin/circle/posts/{post_ids[0]}/pin", headers=h)
    assert r.status_code == 200
    items = client.get("/api/miniapp/circle/posts", headers=mini).json()["items"]
    pinned = [p for p in items if p["is_pinned"]]
    assert len(pinned) == 1 and pinned[0]["id"] == post_ids[0]
    # 馆长赞 1 帖后未赞数-1
    assert (
        client.post(f"/api/admin/circle/posts/{post_ids[0]}/admin-like", headers=h).status_code
        == 200
    )
    unliked = client.get("/api/admin/circle/unliked-count", headers=h).json()
    assert unliked["count"] == 3
    # 运营概览
    ov = client.get("/api/admin/circle/overview", headers=h).json()
    assert ov["week_new_posts"] == 4 and ov["total_likes"] == 1
    assert ov["admin_liked_coverage"] == 25.0


# ---------- 8) Q9：删除后恢复晒权（同成就可重晒） ----------


def test_delete_then_reshare_allowed(client: TestClient):
    """Q9 裁决：同成就"同时至多一条活跃帖"——家长删自己的帖后可重晒，
    超管删帖同样不永久封死（防滥用目标是防刷屏，不是终身封禁）。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000731", "重晒孩", "Reshare")
    ms = _award_milestone(c1, 100000)
    post_id = _share(client, m1, c1, MILESTONE, ms).json()["post_id"]
    # 活跃帖存在时重复晒 → 422
    assert _share(client, m1, c1, MILESTONE, ms).status_code == 422
    # 家长自删 → 恢复晒权
    assert client.delete(f"/api/miniapp/circle/posts/{post_id}", headers=m1).status_code == 200
    r = _share(client, m1, c1, MILESTONE, ms)
    assert r.status_code == 200, r.text
    assert r.json()["post_id"] != post_id
    # 超管删（必填原因）→ 同样恢复晒权
    post_id2 = r.json()["post_id"]
    r = client.request(
        "DELETE",
        f"/api/admin/circle/posts/{post_id2}",
        json={"reason": "重晒口径验证"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert _share(client, m1, c1, MILESTONE, ms).status_code == 200


# ---------- 9) Q8：脏数据多条置顶自愈（不静默丢帖） ----------


def test_multiple_pinned_self_heal(client: TestClient):
    """Q8 ②：并发/脏数据产生多条置顶时，其余置顶帖回落普通流可见，
    不得被 is_pinned==0 过滤静默丢弃。"""
    h = _h(client)
    # 日限按 child 口径：1 家长 3 孩子各晒 1 帖
    p = client.post(
        "/api/admin/members/parents", json={"name": "脏顶家长", "phone": "13800000732"}, headers=h
    ).json()
    mini = {
        "Authorization": (
            f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000732', 'code': '1234'}).json()['token']}"
        )
    }
    post_ids = []
    for i in range(3):
        c = client.post(
            f"/api/admin/members/parents/{p['id']}/children",
            json={"name": f"脏顶孩{i}", "english_name": f"Dirty{i}"},
            headers=h,
        ).json()
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
        ms = _award_milestone(c["id"], 100000 * (i + 1))
        post_ids.append(_share(client, mini, c["id"], MILESTONE, ms).json()["post_id"])
    # 直接改库造脏数据：2 条同时置顶
    from backend.domain.reading_circle.models import CirclePost

    with _db() as db:
        db.query(CirclePost).filter(CirclePost.id == post_ids[0]).update({"is_pinned": 1})
        db.query(CirclePost).filter(CirclePost.id == post_ids[1]).update({"is_pinned": 1})
        db.commit()
    items = client.get("/api/miniapp/circle/posts", headers=mini).json()["items"]
    ids = [p["id"] for p in items]
    assert set(post_ids) <= set(ids), f"多条置顶时不得丢帖: {ids}"
    assert sum(1 for p in items if p["is_pinned"]) == 2  # 两条仍可见（一条在槽、一条回落）
    # 平台口径修正：重新置顶任一帖 → 回到单置顶
    assert client.post(f"/api/admin/circle/posts/{post_ids[2]}/pin", headers=h).status_code == 200
    items = client.get("/api/miniapp/circle/posts", headers=mini).json()["items"]
    assert sum(1 for p in items if p["is_pinned"]) == 1


# ---------- 10) Q10：完读卡 ref_id 语义 = book_id ----------


def test_finish_book_ref_id_is_book_id(client: TestClient):
    """Q10 裁决：ref_id=book_id（同书终身一张）；账目行 id 不再作 ref_id。
    my-cards 枚举的完读卡 ref_id 也必须是 book_id（否则前端晒卡必 422）。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000733", "完读孩", "Finish")
    b1 = _seed_book(client, h, "9788400000021", 320)
    ledger_id = _credit_words(c1, b1, 320)
    # my-cards 枚举口径 = book_id
    cards = client.get(f"/api/miniapp/circle/my-cards?child_id={c1}", headers=m1).json()
    finish_refs = [c["ref_id"] for c in cards["available"] if c["card_type"] == FINISH_BOOK]
    assert finish_refs == [b1], f"完读卡 ref_id 应为 book_id: {finish_refs}"
    # 用 book_id 晒 → 200
    assert _share(client, m1, c1, FINISH_BOOK, b1).status_code == 200
    # 用账目行 id 晒 → 422（语义已切换，防回归）
    if ledger_id != b1:
        assert _share(client, m1, c1, FINISH_BOOK, ledger_id).status_code == 422


# ==================== WM14-B 二期 ====================

RANK_TOP = "rank_top"
RANK_UP = "rank_up"
WEEKLY_REPORT = "weekly_report"
BREAKTHROUGH = "breakthrough"


def _credit_words_at(child_id: int, book_id: int, words: int, when) -> int:
    """指定时间点的词账（真链路：word_count 取书目总词数由调用方保证）。"""
    from backend.domain.growth.models import WordsLedger

    with _db() as db:
        row = WordsLedger(child_id=child_id, book_id=book_id, word_count=words, created_at=when)
        db.add(row)
        db.commit()
        return row.id


def _monday_of(d=None):
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def _snapshot(today=None) -> int:
    from backend.domain.reading_circle.snapshot_service import CircleSnapshotService

    with _db() as db:
        return CircleSnapshotService(db).run_weekly_snapshot(today=today)


def _cards_of(client: TestClient, mini: dict, child_id: int) -> dict:
    return client.get(f"/api/miniapp/circle/my-cards?child_id={child_id}", headers=mini).json()


def _in(bucket: list, card_type: str, ref_id: int | None = None):
    for c in bucket:
        if c["card_type"] == card_type and (ref_id is None or c["ref_id"] == ref_id):
            return c
    return None


def test_rank_snapshot_idempotent_and_same_source_as_board(client: TestClient):
    """B1：快照幂等（第二遍 0 新增）+ 名次/词数与周榜口径逐条一致（计数同源机器化）。"""
    h = _h(client)
    c1, _ = _mk_parent_with_child(client, h, "13800000801", "榜孩A", "RankA")
    b1 = _seed_book(client, h, "9788400000901", 900)
    b2 = _seed_book(client, h, "9788400000902", 300)
    lm = _monday_of() - timedelta(days=7)  # 上一个完整自然周（周一）
    _credit_words_at(c1, b1, 900, datetime.combine(lm + timedelta(days=1), datetime.min.time()))
    _credit_words_at(c1, b2, 300, datetime.combine(lm + timedelta(days=2), datetime.min.time()))

    assert _snapshot(today=date.today()) == 1
    assert _snapshot(today=date.today()) == 0  # 幂等：同周重跑不再落库

    from backend.domain.growth.board_service import LeaderboardService
    from backend.domain.reading_circle.models import CircleRankSnapshot

    with _db() as db:
        start = datetime.combine(lm, datetime.min.time())
        end = datetime.combine(lm + timedelta(days=7), datetime.min.time())
        entries = LeaderboardService(db).period_entries(start, end)
        snaps = (
            db.query(CircleRankSnapshot)
            .filter(CircleRankSnapshot.week_start == lm)
            .order_by(CircleRankSnapshot.rank)
            .all()
        )
    assert [(s.child_id, s.rank, s.words) for s in snaps] == [
        (e["child_id"], i + 1, e["words"]) for i, e in enumerate(entries)
    ], "快照名次/词数必须与周榜口径逐条一致（计数同源）"


def test_rank_top_and_rank_up_cards(client: TestClient):
    """B2：上榜卡（TOP10）与上升卡（本周 rank < 上周 rank）；新上榜不产生上升卡。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000802", "升孩", "Riser")
    c2, m2 = _mk_parent_with_child(client, h, "13800000803", "降孩", "Faller")
    books = [
        _seed_book(client, h, f"978840000091{i}", w) for i, w in enumerate([9000, 5000, 1200, 300])
    ]
    lw = _monday_of() - timedelta(days=7)  # 上一个完整周
    pw = lw - timedelta(days=7)  # 再往前一周
    # 上上周：降孩 9000 > 升孩 300（升孩 rank2）；上周：升孩 9000 > 降孩 300（升孩 rank1）
    _credit_words_at(
        c1, books[3], 300, datetime.combine(pw + timedelta(days=1), datetime.min.time())
    )
    _credit_words_at(
        c2, books[0], 9000, datetime.combine(pw + timedelta(days=1), datetime.min.time())
    )
    _credit_words_at(
        c1, books[0], 9000, datetime.combine(lw + timedelta(days=1), datetime.min.time())
    )
    _credit_words_at(
        c2, books[3], 300, datetime.combine(lw + timedelta(days=1), datetime.min.time())
    )
    assert _snapshot(today=lw + timedelta(days=3)) == 2  # 结算上上周
    assert _snapshot(today=date.today()) == 2  # 结算上周

    cards = _cards_of(client, m1, c1)
    top = _in(cards["available"], RANK_TOP, int(lw.strftime("%Y%m%d")))
    assert top and "第 1 名" in top["title"], cards["available"]
    up = _in(cards["available"], RANK_UP, int(lw.strftime("%Y%m%d")))
    assert up and "↑1 位" in up["title"], cards["available"]
    # 降孩：上周第 2 → 上榜但无上升卡（名次下降）
    cards2 = _cards_of(client, m2, c2)
    assert _in(cards2["available"], RANK_TOP, int(lw.strftime("%Y%m%d")))
    assert _in(cards2["available"], RANK_UP, int(lw.strftime("%Y%m%d"))) is None
    # 晒卡链路（写路径）：上升卡可晒，伪造成本周 ref_id → 422
    assert _share(client, m1, c1, RANK_UP, int(lw.strftime("%Y%m%d"))).status_code == 200
    assert _share(client, m1, c1, RANK_UP, 20990101).status_code == 422


def test_weekly_report_card(client: TestClient):
    """B3：周报卡取上一完整周（区间口径同 ReportService），ref_id=上周一 YYYYMMDD。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000804", "周报孩", "Weekly")
    b1 = _seed_book(client, h, "9788400000921", 700)
    b2 = _seed_book(client, h, "9788400000922", 500)
    lw = _monday_of() - timedelta(days=7)  # 上一个完整周（周报卡的区间）
    when = datetime.combine(lw + timedelta(days=2), datetime.min.time())
    _credit_words_at(c1, b1, 700, when)
    _credit_words_at(c1, b2, 500, when)

    cards = _cards_of(client, m1, c1)
    rep = _in(cards["available"], WEEKLY_REPORT, int(lw.strftime("%Y%m%d")))
    assert rep and "1,200 词" in rep["title"], cards["available"]
    resp = _share(client, m1, c1, WEEKLY_REPORT, int(lw.strftime("%Y%m%d")))
    assert resp.status_code == 200, resp.text
    # 未来周/非周一 → 422
    assert _share(client, m1, c1, WEEKLY_REPORT, 20990101).status_code == 422
    assert _share(client, m1, c1, WEEKLY_REPORT, 20260909).status_code == 422


def test_breakthrough_card_threshold_and_renewal(client: TestClient):
    """B4：单日突破卡——阈值下不成卡；更高单日出现后 ref_id 更新（不断超越自己）。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000805", "突破孩", "Break")
    b1 = _seed_book(client, h, "9788400000931", 800)
    b2 = _seed_book(client, h, "9788400000932", 3000)
    day = _monday_of() - timedelta(days=5)  # 任意历史日
    _credit_words_at(c1, b1, 800, datetime.combine(day, datetime.min.time()))
    cards = _cards_of(client, m1, c1)
    assert _in(cards["available"], BREAKTHROUGH) is None, "800 词未达阈值 1000，不应成卡"

    day2 = day + timedelta(days=1)
    _credit_words_at(c1, b2, 3000, datetime.combine(day2, datetime.min.time()))
    cards = _cards_of(client, m1, c1)
    card = _in(cards["available"], BREAKTHROUGH, int(day2.strftime("%Y%m%d")))
    assert card and "3,000 词" in card["title"], cards["available"]
    assert _share(client, m1, c1, BREAKTHROUGH, int(day2.strftime("%Y%m%d"))).status_code == 200


def test_banner_same_source_as_week_board(client: TestClient):
    """B5：社区横幅两数字与周榜口径同源（在会 + 本周有入账）。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000806", "横幅孩", "Banner")
    b1 = _seed_book(client, h, "9788400000941", 600)
    _credit_words_at(c1, b1, 600, datetime.now())  # 本周（今天）
    res = client.get("/api/miniapp/circle/posts", headers=m1).json()
    assert res["banner"]["words"] >= 600 and res["banner"]["kids"] >= 1

    from backend.domain.growth.board_service import LeaderboardService

    with _db() as db:
        monday = datetime.combine(_monday_of(), datetime.min.time())
        entries = LeaderboardService(db).period_entries(monday)
        expect_words = sum(e["words"] for e in entries)
    assert res["banner"] == {"words": expect_words, "kids": len(entries)}


def test_display_name_three_consumers(client: TestClient):
    """B6/Q11 称呼消费端（信息流署名 / 管理端署名）。

    fix33 R2 变更：被赞通知文案不再取家长称呼——社交主体切到孩子后，文案是
    「{点赞孩子英文名} 赞了 {帖主孩子英文名} 的成就」，家长 display_name 只剩
    署名两端（此处继续断言，防「媒体消费点断链」回潮）。
    """
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000807", "称呼孩", "Nick")
    c2, m2 = _mk_parent_with_child(client, h, "13800000808", "点赞孩2", "Liker2")
    ms = _award_milestone(c1, 100000)
    post_id = _share(client, m1, c1, MILESTONE, ms).json()["post_id"]

    # 未设称呼 → 回退真实姓名（测试造数家长名统一「圈家长」）
    items = client.get("/api/miniapp/circle/posts", headers=m1).json()["items"]
    assert next(p for p in items if p["id"] == post_id)["parent_name"] == "圈家长"
    # 帖主设称呼 → 信息流署名 / 管理端署名同步（消费端 1、2）
    with _db() as db:
        from backend.domain.identity.models import Parent

        owner = db.query(Parent).filter(Parent.phone == "13800000807").first()
        owner.display_name = "Nick妈妈"
        liker = db.query(Parent).filter(Parent.phone == "13800000808").first()
        liker.display_name = "Liker妈妈"
        db.commit()
    items = client.get("/api/miniapp/circle/posts", headers=m1).json()["items"]
    assert next(p for p in items if p["id"] == post_id)["parent_name"] == "Nick妈妈"
    adm = client.get("/api/admin/circle/posts", headers=h).json()["items"]
    assert next(p for p in adm if p["id"] == post_id)["parent_name"] == "Nick妈妈"
    # 被赞通知文案取**点赞孩子**名义（fix33 R2：家长不参与社交，认孩子不认家长称呼）
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    notes = client.get("/api/miniapp/notifications?category=其他", headers=m1).json()["items"]
    assert any("Liker2 赞了 Nick 的成就" in n["content"] for n in notes), notes
    assert not any("Liker妈妈" in n["content"] for n in notes), notes  # 家长称呼不进通知
    # 自助改称呼端点（空串=回退）
    r = client.put("/api/miniapp/parent/profile", json={"display_name": "Nick爸"}, headers=m1)
    assert r.status_code == 200 and r.json()["display_name"] == "Nick爸"
    r = client.put("/api/miniapp/parent/profile", json={"display_name": ""}, headers=m1)
    assert r.json()["display_name"] == "" and r.json()["name"] == "圈家长"


def test_orphan_card_image_cleanup(client: TestClient):
    """C3/Q13：删帖超 30 天的卡片图物理清理；未到期不动；幂等。"""
    import os
    from datetime import timedelta

    from backend.config import get_settings
    from backend.domain.reading_circle.admin_service import CircleImageCleanupService
    from backend.domain.reading_circle.models import CirclePost

    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000809", "清理孩", "Clean")
    ms = _award_milestone(c1, 100000)
    post_id = _share(client, m1, c1, MILESTONE, ms).json()["post_id"]
    assert client.delete(f"/api/miniapp/circle/posts/{post_id}", headers=m1).status_code == 200

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        rel = post.image_path
        full = os.path.join(root, rel)
        assert os.path.isfile(full), "晒卡必须真渲染落盘（真链路）"

        # 未到期：不改动
        assert CircleImageCleanupService(db).cleanup_orphan_images() == 0
        assert os.path.isfile(full)
        # 回填 update_time 到 31 天前 → 清理
        post.update_time = datetime.now() - timedelta(days=31)
        db.commit()
    with _db() as db:
        assert CircleImageCleanupService(db).cleanup_orphan_images() == 1
    assert not os.path.isfile(full), "到期孤儿图应被物理删除"
    with _db() as db:
        post = db.query(CirclePost).filter(CirclePost.id == post_id).first()
        assert post.image_path == ""  # 置空防重复清理
        assert CircleImageCleanupService(db).cleanup_orphan_images() == 0  # 幂等


def test_assemble_card_data_fields_all_types(client: TestClient):
    """C2 补测：A 期 6 类卡 card_data 关键字段装配正确（不只测 422 归属）。"""
    from backend.domain.reading_circle import card_engine

    h = _h(client)
    c1, _ = _mk_parent_with_child(client, h, "13800000810", "装配孩", "Assemble")
    b1 = _seed_book(client, h, "9788400000951", 420)
    ms = _award_milestone(c1, 100000)
    qa = _award_perfect_quiz(c1, b1)
    st = _award_streak(c1, "week", 7)
    wl = _credit_words(c1, b1, 420)

    with _db() as db:
        from backend.domain.identity.models import Child

        child = db.query(Child).filter(Child.id == c1).first()
        d_ms = card_engine.assemble_card_data(db, child, MILESTONE, ms)
        assert d_ms["value_text"] == "10 万" and d_ms["title"] == "里程碑达成"
        d_book = card_engine.assemble_card_data(db, child, FINISH_BOOK, b1)
        assert "+420 词" in d_book["value_text"] and "Circle" in d_book["value_label"]
        d_quiz = card_engine.assemble_card_data(db, child, PERFECT_QUIZ, qa)
        assert d_quiz["value_text"] == "5/5"
        d_streak = card_engine.assemble_card_data(db, child, STREAK, st)
        assert d_streak["value_text"] == "7 天"
        # 归属校验：他人孩子的成就 → 422
        from backend.common.exceptions import ValidationError as _VE
        from backend.domain.identity.models import Child as C

        c2, _ = _mk_parent_with_child(client, h, "13800000811", "别人孩", "Other2")
        other = db.query(C).filter(C.id == c2).first()
        for ct, ref in ((MILESTONE, ms), (PERFECT_QUIZ, qa), (STREAK, st), (FINISH_BOOK, b1)):
            try:
                card_engine.assemble_card_data(db, other, ct, ref)
            except _VE:
                continue
            raise AssertionError(f"{ct} 归属校验失效：他人孩子成就竟装配成功")
    _ = wl


def test_admin_overview_metrics(client: TestClient):
    """C2 补测：运营概览五指标口径（周新帖/分享家长/点赞总数/馆长赞覆盖率/类型分布）。"""
    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13800000812", "概览孩A", "OvA")
    c2, m2 = _mk_parent_with_child(client, h, "13800000813", "概览孩B", "OvB")
    p1 = _share(client, m1, c1, MILESTONE, _award_milestone(c1, 100000)).json()["post_id"]
    _share(client, m2, c2, MILESTONE, _award_milestone(c2, 100000))
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{p1}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    assert client.post(f"/api/admin/circle/posts/{p1}/admin-like", headers=h).status_code == 200

    ov = client.get("/api/admin/circle/overview", headers=h).json()
    assert ov["week_new_posts"] == 2
    assert ov["sharing_parents"] == 2
    assert ov["total_likes"] == 2  # 家长赞 +1、馆长赞 +1 均计入 like_count
    assert ov["admin_liked_coverage"] == 50.0
    assert ov["card_type_distribution"] == {"里程碑": 2}
