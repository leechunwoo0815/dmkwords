# tests/unit/test_fix34_circle.py — fix34 阅读圈 B 期数据面（真实链路）
"""覆盖：
- R4 等级头像框数据面：信息流 item.level / 点赞墙 liker.level / 榜单 entry.level（批查，禁 N+1）
- R0 通知按场景过滤（scene=circle.liked）——阅读圈「谁赞了你」通知条的数据源
- R5 里程碑卡「全馆第 N 位达成」：名次按达成时刻排序、含本人、冻结进 card_data
- R6 名片页 is_birthday：只回布尔，**不泄露生日日期**
- R2 海报改 JPEG：content-type / 体积 ≤250KB / 旧 .png 被清掉
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

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


def _award_milestone(child_id: int, node_words: int, at: datetime | None = None) -> int:
    from backend.domain.growth.models import MilestoneAward

    with _db() as db:
        row = MilestoneAward(
            child_id=child_id, node_words=node_words, awarded_at=at or datetime.now()
        )
        db.add(row)
        db.commit()
        return row.id


def _set_level(child_id: int, level: str) -> None:
    from backend.domain.growth.models import ChildGrowthState

    with _db() as db:
        st = db.query(ChildGrowthState).filter(ChildGrowthState.child_id == child_id).first()
        if st:
            st.level = level
        else:
            db.add(ChildGrowthState(child_id=child_id, level=level))
        db.commit()


def _seed_book(client: TestClient, h: dict, isbn: str, words: int) -> int:
    return client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": f"Fix34{isbn[-3:]}", "word_count": words},
        headers=h,
    ).json()["id"]


def _credit_words(child_id: int, book_id: int, words: int) -> None:
    from backend.domain.growth.models import WordsLedger

    with _db() as db:
        db.add(
            WordsLedger(
                child_id=child_id, book_id=book_id, word_count=words, created_at=datetime.now()
            )
        )
        db.commit()


def _share(client: TestClient, mini: dict, child_id: int, ref_id: int):
    return client.post(
        "/api/miniapp/circle/posts",
        json={"child_id": child_id, "card_type": MILESTONE, "ref_id": ref_id},
        headers=mini,
    )


# ---------- R4：等级头像框数据面（三个消费端） ----------


def test_level_delivered_to_feed_likers_and_board(client: TestClient):
    """信息流 item.level / 点赞墙 liker.level / 榜单 entry.level —— 三端都拿得到等级。"""
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000941", "等级孩", "LvKid")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800000942", "点赞孩甲", "FanA")
    _set_level(c1, "F")
    _set_level(c2, "C")
    # 榜单只收 words>0 的孩子 → 先入账一笔，否则条目为空（计数口径见 board_service）
    _credit_words(c1, _seed_book(client, h, "9788400000901", 900), 900)
    _credit_words(c2, _seed_book(client, h, "9788400000902", 500), 500)
    post_id = _share(client, m1, c1, _award_milestone(c1, 100000)).json()["post_id"]
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )

    item = next(
        p
        for p in client.get(f"/api/miniapp/circle/posts?child_id={c2}", headers=m1).json()["items"]
        if p["id"] == post_id
    )
    assert item["level"] == "F"  # 发帖孩子等级
    assert item["likers"][0]["level"] == "C"  # 点赞孩子等级（头像墙同款）

    # 榜单：有效会员入榜后条目带等级（批查注入）
    board = client.get(f"/api/miniapp/leaderboard?period=total&child_id={c1}", headers=m1).json()
    rows = board["entries"]
    assert rows, board
    assert all(e["level"] for e in rows), rows[:2]  # 每个上榜条目都带等级（头像框数据面）
    assert any(e["child_id"] == c1 and e["level"] == "F" for e in rows), rows[:2]


# ---------- R0：通知按场景过滤 ----------


def test_notifications_scene_filter(client: TestClient):
    """scene=circle.liked 只回本场景（顶部通知条数据源），不夹带其他场景。"""
    from backend.common.notification_models import Notification

    h = _h(client)
    c1, m1, pid = _mk_parent_with_child(client, h, "13800000943", "通知孩", "NoticeKid")
    c2, _, _ = _mk_parent_with_child(client, h, "13800000948", "点赞孩甲", "FanKid")
    _set_level(c2, "C")
    with _db() as db:
        db.add(
            Notification(
                parent_id=pid,
                child_id=c1,
                scene="circle.liked",
                category="其他",
                title="收到点赞",
                content="点赞孩甲 赞了你的成就",
                ref_type="child",  # 行为主体=点赞孩子 → 列表要能显示 TA 的头像/等级
                ref_id=str(c2),
            )
        )
        db.add(
            Notification(
                parent_id=pid,
                child_id=c1,
                scene="borrow.success",
                category="借阅",
                title="借书成功",
                content="借书成功 内容",
            )
        )
        db.commit()

    body = client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()
    assert body["total"] == 1 and all(i["scene"] == "circle.liked" for i in body["items"])
    # fix34 R0：通知项带"行为主体"（点赞者）头像/等级 → 顶部通知条直接渲染头像堆叠
    assert (
        body["items"][0]["actor_avatar"] == "bunny_mint" or body["items"][0]["actor_avatar"] is None
    )
    assert body["items"][0]["actor_level"] == "C"
    # fix34e4：展示名由后端给（前端不再从文案 split —— 馆长文案无空格会切错）
    assert body["items"][0]["actor_name"] == "FanKid"  # 圈内展示名=英文名（与榜单/署名同口径）
    # 未过滤时还有别的场景（证明过滤真的在生效，而不是库里本来就只有这一条）
    allbody = client.get("/api/miniapp/notifications", headers=m1).json()
    assert allbody["total"] > body["total"]
    assert any(i["scene"] != "circle.liked" for i in allbody["items"])


# ---------- R5：全馆第 N 位达成 ----------


def test_milestone_hall_rank_by_award_time_and_frozen(client: TestClient):
    """先达成者第 1 位、后达成者第 2 位；名次随 card_data 冻结（不随后续新达成变动）。"""
    from backend.domain.reading_circle import card_engine

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000944", "首达孩", "First")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800000945", "次达孩", "Second")
    early = datetime.now() - timedelta(hours=5)
    late = datetime.now() - timedelta(hours=1)
    _award_milestone(c1, 100000, at=early)
    aid2 = _award_milestone(c2, 100000, at=late)

    with _db() as db:
        from backend.domain.identity.models import Child

        kid2 = db.query(Child).filter(Child.id == c2).first()
        data2 = card_engine.assemble_card_data(db, kid2, MILESTONE, aid2)
        assert data2["hall_rank"] == 2
        assert "全馆第 2 位达成" in data2["value_label"]
        # 只有一人时名次为 1（含本人）
        kid1 = db.query(Child).filter(Child.id == c1).first()
        aid1 = (
            db.query(card_engine.MilestoneAward)
            .filter(card_engine.MilestoneAward.child_id == c1)
            .first()
            .id
        )
        data1 = card_engine.assemble_card_data(db, kid1, MILESTONE, aid1)
        assert data1["hall_rank"] == 1

    # 晒卡 → 播报随 card_data 冻结进帖子，并出现在**信息流原生副标题**（列表页可见）
    post_id = _share(client, m2, c2, aid2).json()["post_id"]
    item = next(
        p
        for p in client.get(f"/api/miniapp/circle/posts?child_id={c2}", headers=m2).json()["items"]
        if p["id"] == post_id
    )
    assert "全馆第 2 位达成" in item["subtitle"], item["subtitle"]


# ---------- R6：生日彩蛋（布尔，不泄露日期） ----------


def test_profile_is_birthday_flag_without_date_leak(client: TestClient):
    from backend.domain.identity.models import Child

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000946", "生日孩", "BdayKid")
    today = datetime.now().date()

    def _set_birthday(d):
        with _db() as db:
            ch = db.query(Child).filter(Child.id == c1).first()
            ch.birthday = d
            db.commit()

    with _db():  # 生日为空 → False（且不报错）
        assert (
            client.get(f"/api/miniapp/circle/children/{c1}/profile", headers=m1).json()[
                "is_birthday"
            ]
            is False
        )
    _set_birthday(today.replace(year=today.year - 6))
    body = client.get(f"/api/miniapp/circle/children/{c1}/profile", headers=m1).json()
    assert body["is_birthday"] is True
    # 隐私红线：响应里不得出现生日日期本身
    assert str(today.year - 6) not in str(body)
    _set_birthday(today.replace(year=today.year - 6) - timedelta(days=1))
    assert (
        client.get(f"/api/miniapp/circle/children/{c1}/profile", headers=m1).json()["is_birthday"]
        is False
    )


# ---------- R2：海报 JPEG 压缩 ----------


def test_poster_jpeg_and_under_250kb(client: TestClient):
    from backend.config import get_settings

    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000947", "海报孩", "PosterKid")
    token = m1["Authorization"].split()[1]
    r = client.get(f"/api/miniapp/circle/children/{c1}/poster", params={"token": token})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert len(r.content) <= 250 * 1024, f"海报 {len(r.content) / 1024:.0f}KB 超过 250KB"
    assert r.content[:2] == b"\xff\xd8"  # JPEG magic

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    assert os.path.isfile(os.path.join(root, "posters", f"poster_{c1}.jpg"))
    assert not os.path.isfile(os.path.join(root, "posters", f"poster_{c1}.png"))  # 旧 png 已清


# ---------- fix34d：馆长（GM）进点赞墙 ----------


def test_admin_like_shows_curator_first_in_wall(client: TestClient):
    """馆长赞 → 头像墙第一位是「馆长」（is_admin，金光段），且计数与墙上人数口径一致。

    馆长的赞不建 CircleLike 行（只落 admin_liked），故此处验证"合成条目"确实补上了墙。
    """
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000951", "被赞孩甲", "GmKid")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800000952", "点赞孩乙", "FanKid2")
    post_id = _share(client, m1, c1, _award_milestone(c1, 100000)).json()["post_id"]

    # 馆长行内赞
    assert (
        client.post(f"/api/admin/circle/posts/{post_id}/admin-like", headers=h).status_code == 200
    )
    item = next(
        p
        for p in client.get(f"/api/miniapp/circle/posts?child_id={c1}", headers=m1).json()["items"]
        if p["id"] == post_id
    )
    assert item["like_count"] == 1
    assert len(item["likers"]) == 1
    gm = item["likers"][0]
    assert gm["is_admin"] is True and gm["name"] == "馆长" and gm["level"] == "GM"
    assert gm["avatar"] is None  # 前端凭 is_admin 换 /icons/special/gm_avatar.png

    # 孩子再赞 → 馆长仍排第一，计数与墙上人数口径一致（like_count = 墙上人数 ≤8 时）
    assert (
        client.post(
            f"/api/miniapp/circle/posts/{post_id}/like", json={"child_id": c2}, headers=m2
        ).status_code
        == 200
    )
    item = next(
        p
        for p in client.get(f"/api/miniapp/circle/posts?child_id={c1}", headers=m1).json()["items"]
        if p["id"] == post_id
    )
    assert item["like_count"] == 2
    assert [x["is_admin"] for x in item["likers"]] == [True, False]
    assert item["likers"][1]["child_id"] == c2

    # 取消馆长赞 → 合成条目随之消失（不残留）
    assert (
        client.delete(f"/api/admin/circle/posts/{post_id}/admin-like", headers=h).status_code == 200
    )
    item = next(
        p
        for p in client.get(f"/api/miniapp/circle/posts?child_id={c1}", headers=m1).json()["items"]
        if p["id"] == post_id
    )
    assert all(not x["is_admin"] for x in item["likers"]) and item["like_count"] == 1


def test_admin_like_notification_ref_type_and_per_post_dedup(client: TestClient):
    """fix34e：馆长赞通知 ref_type=circle_admin（前端凭此弹金光播报）；且**逐帖**各发一条。

    去重键 = parent+scene+ref_type+ref_id+dedup_key：馆长赞 ref_id=帖子 id → 两帖各一条；
    孩子赞的 ref_id 是"点赞孩子"（供深链），帖子维度只能落在 dedup_key 上
    （旧键只含孩子 id → 同一孩子赞你两帖只通知一条，已修）。
    """
    h = _h(client)
    # 一个家长 + 两个孩子（两帖同属一位家长，才能在同一份通知列表里核对）
    c1, m1, pid = _mk_parent_with_child(client, h, "13800000961", "播报孩甲", "CastA")
    c2 = client.post(
        f"/api/admin/members/parents/{pid}/children",
        json={"name": "播报孩乙", "english_name": "CastB"},
        headers=h,
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c2["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    p1 = _share(client, m1, c1, _award_milestone(c1, 100000)).json()["post_id"]
    p2 = _share(client, m1, c2["id"], _award_milestone(c2["id"], 200000)).json()["post_id"]

    # 馆长赞两帖 → 该家长各收一条，ref_type 指明是馆长、ref_id 是帖子
    for pid_ in (p1, p2):
        assert (
            client.post(f"/api/admin/circle/posts/{pid_}/admin-like", headers=h).status_code == 200
        )
    notes = client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()["items"]
    admin_notes = [n for n in notes if n["ref_type"] == "circle_admin"]
    assert {n["ref_id"] for n in admin_notes} == {str(p1), str(p2)}, admin_notes
    assert all("馆长" in n["content"] for n in admin_notes)

    # 另一个家长的孩子**连赞这两帖** → 帖主各收一条（旧键会被吞掉第二条）
    c3, m3, _ = _mk_parent_with_child(client, h, "13800000963", "连赞孩", "TwoKid")
    for pid_ in (p1, p2):
        assert (
            client.post(
                f"/api/miniapp/circle/posts/{pid_}/like", json={"child_id": c3}, headers=m3
            ).status_code
            == 200
        )
    notes = client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()["items"]
    kid_notes = [n for n in notes if n["ref_type"] == "child"]
    assert len(kid_notes) == 2, [n["content"] for n in kid_notes]  # 两帖各一条（去重键含 post）


def test_admin_like_notification_actor_name_is_curator(client: TestClient):
    """fix34e4：馆长赞通知的 actor_name 固定为「馆长」（前端展示名不靠文案切分）。

    回归背景：前端原用 content.split(' 赞了')[0] 取名字，而馆长文案是
    「馆长赞了 X 的成就」（无空格）→ 切不出来 → 横幅显示成
    「馆长赞了 Demo 的成就 赞了你的成就」（用户实测）。
    """
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000971", "馆长名孩", "CurName")
    post_id = _share(client, m1, c1, _award_milestone(c1, 100000)).json()["post_id"]
    assert (
        client.post(f"/api/admin/circle/posts/{post_id}/admin-like", headers=h).status_code == 200
    )
    note = next(
        n
        for n in client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()[
            "items"
        ]
        if n["ref_type"] == "circle_admin"
    )
    assert note["actor_name"] == "馆长"


def test_admin_note_not_given_child_avatar_on_id_collision(client: TestClient):
    """fix34e5：馆长赞通知的 ref_id 是**帖子 id**，不能被当成孩子 id 去配头像。

    回归背景：演示现场帖子 1 / 孩子 1 同号 → 通知列表里馆长条目显示成"别人的头像"
    （用户实测："馆长赞了我的成就，头像用我自己的不合适"）。
    约定：孩子维度字段（actor_avatar/actor_level/actor_name）**只对 ref_type=child 生效**；
    馆长条目的头像由前端凭 ref_type 换成金光馆长资产。
    """
    h = _h(client)
    c1, m1, _ = _mk_parent_with_child(client, h, "13800000981", "撞号孩甲", "ClashA")
    c2, m2, _ = _mk_parent_with_child(client, h, "13800000982", "撞号孩乙", "ClashB")
    p1 = _share(client, m1, c1, _award_milestone(c1, 100000)).json()["post_id"]
    # 让"孩子 id"与"帖子 id"撞上：先把帖子 id 记下，再拿同号的孩子去点赞（同号未必可得，
    # 故直接断言"馆长条目永远不带孩子维度字段"这一条不变量）
    client.post(f"/api/miniapp/circle/posts/{p1}/like", json={"child_id": c2}, headers=m2)
    assert client.post(f"/api/admin/circle/posts/{p1}/admin-like", headers=h).status_code == 200
    items = client.get("/api/miniapp/notifications?scene=circle.liked", headers=m1).json()["items"]
    admin_note = next(n for n in items if n["ref_type"] == "circle_admin")
    assert admin_note["actor_name"] == "馆长"
    assert admin_note["actor_avatar"] is None  # 绝不配孩子头像（前端换金光馆长资产）
    assert admin_note["actor_level"] is None
    kid_note = next(n for n in items if n["ref_type"] == "child")
    # 孩子赞：展示名=英文名，且等级字段有值（本用例孩子未设头像，故 avatar 合法为 None）
    assert kid_note["actor_name"] == "ClashB"
    assert kid_note["actor_level"] == "A"
