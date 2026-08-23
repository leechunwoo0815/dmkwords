# tests/unit/test_wm7_growth.py — 测验与成长（真实链路：完播→测验→词数→积分→等级→里程碑）
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _setup_finished_book(client, h, phone, isbn, word_count=2500, duration=600):
    """会员孩子 + 押金 + 完播一本书（600s）+ 5 道题 + 小程序登录。返回 (child, book, mini_h)。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "成长孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    book = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": f"Book {isbn[-4:]}", "word_count": word_count},
        headers=h,
    ).json()
    # 5 道题（答案分别为 A/A/A/A/对）
    for i in range(1, 5):
        client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i}?",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
                "sort_order": i,
            },
            headers=h,
        )
    client.post(
        f"/api/admin/books/{book['id']}/questions",
        json={
            "question_type": "boolean",
            "question_text": "Q5?",
            "options": ["对", "错"],
            "answer": "对",
            "sort_order": 5,
        },
        headers=h,
    )
    # 完播（时间回拨模拟真实流逝）
    import io

    mp3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
    client.post(
        f"/api/admin/books/{book['id']}/audio",
        files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=h,
    )
    from backend.database import get_session
    from backend.domain.catalog.models import Book as BookModel

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book["id"]).first()
        b.audio_duration_seconds = duration
        db.commit()
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book["id"],
            "position": 10,
            "session_start": 0,
        },
        headers=mini,
    )
    from backend.database import get_session
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        prog = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == c["id"], ReadingProgress.book_id == book["id"])
            .first()
        )
        prog.last_report_at = datetime.now() - timedelta(seconds=590)
        db.commit()
    client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book["id"],
            "position": 600,
            "session_start": 10,
        },
        headers=mini,
    )
    return c, book, mini


def test_quiz_flow_and_words(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000701", "9787100000001")
    # 1) 解锁 + 拉题（不带答案）
    q = client.get(f"/api/miniapp/quiz/{book['id']}?child_id={c['id']}", headers=mini).json()
    assert q["unlocked"] is True
    assert q["status"] == "available"
    assert len(q["questions"]) == 5
    assert "answer" not in q["questions"][0]
    # 2) 4/5 通过 → 词数 +2500
    r = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "错"],
        },
        headers=mini,
    ).json()
    assert r["score"] == 4 and r["passed"] is True
    assert r["just_passed"] is True
    assert r["words_added"] == 2500
    # 积分：2500 词 → 25 分（零头 0）+ 首过 5 分
    types = {d["type"]: d["points"] for d in r["points_detail"]}
    assert types["words_convert"] == 25
    assert types["quiz_first_pass"] == 5
    # 3) 重测同书更低分 → 最高分保留、词数不重复
    r2 = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "B", "B", "B", "错"],
        },
        headers=mini,
    ).json()
    assert r2["score"] == 1 and r2["passed"] is False
    assert r2["best_score"] == 4
    assert r2["just_passed"] is False
    # 4) 管理端成长档案
    g = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
    assert g["summary"]["words_total"] == 2500
    assert g["summary"]["books_total"] == 1
    assert g["summary"]["points_total"] == 30
    assert g["words_ledger"][0]["title"] == "Book 0001"
    assert len(g["points_ledger"]) == 2
    assert g["quiz_overview"][0]["attempts_used"] == 2


def test_quiz_three_fails_and_admin_reset(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000702", "9787100000002")
    # 连败 3 次
    for _ in range(3):
        r = client.post(
            f"/api/miniapp/quiz/{book['id']}/submit",
            json={
                "child_id": c["id"],
                "answers": ["B", "B", "B", "B", "错"],
            },
            headers=mini,
        )
        assert r.status_code == 200
    # 状态 failed + 第 4 次被拒
    q = client.get(f"/api/miniapp/quiz/{book['id']}?child_id={c['id']}", headers=mini).json()
    assert q["status"] == "failed"
    assert q["attempts_left"] == 0
    r4 = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "对"],
        },
        headers=mini,
    )
    assert r4.status_code == 422
    # 词数未入账
    g = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
    assert g["summary"]["words_total"] == 0
    # staff01 不能重置
    hs = _h(client, "staff01")
    r_denied = client.post(
        "/api/admin/quiz/attempts/reset",
        json={
            "child_id": c["id"],
            "book_id": book["id"],
            "reason": "孩子当时生病",
        },
        headers=hs,
    )
    assert r_denied.status_code == 403
    # admin 重置 → 恢复 3 次
    rr = client.post(
        "/api/admin/quiz/attempts/reset",
        json={
            "child_id": c["id"],
            "book_id": book["id"],
            "reason": "孩子当时生病状态差",
        },
        headers=h,
    )
    assert rr.status_code == 200
    assert rr.json()["attempts_left"] == 3
    # 重测通过 → 正常计词数；测验积分只发一次（首过 5 分仍在）
    r5 = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "对"],
        },
        headers=mini,
    ).json()
    assert r5["passed"] is True and r5["words_added"] == 2500
    types = {d["type"]: d["points"] for d in r5["points_detail"]}
    assert types.get("words_convert") == 25
    # 前 3 次全败从未发过测验奖励 → 重置后首次通过且满分发 +10（与 +5 互斥取高）
    assert types.get("quiz_full_marks") == 10
    assert "quiz_first_pass" not in types
    g2 = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
    assert g2["summary"]["words_total"] == 2500


def test_quiz_locked_without_finish(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000703", "9787100000003")
    # 换一本没读完的书
    other = client.post(
        "/api/admin/books",
        json={"isbn": "9787100000013", "title": "Locked", "word_count": 100},
        headers=h,
    ).json()
    q = client.get(f"/api/miniapp/quiz/{other['id']}?child_id={c['id']}", headers=mini).json()
    assert q["unlocked"] is False
    assert q["status"] == "locked"
    r = client.post(
        f"/api/miniapp/quiz/{other['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A"],
        },
        headers=mini,
    )
    assert r.status_code == 422


def test_points_remainder_pool_and_full_marks(client: TestClient):
    """零头池：450 词 +4 余 50；560 词 → 50+560=610 → +6 余 10。满分 +10 与首过互斥。"""
    h = _h(client)
    # 第一本 450 词
    c, book1, mini = _setup_finished_book(client, h, "13800000704", "9787100000004", word_count=450)
    r1 = client.post(
        f"/api/miniapp/quiz/{book1['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "对"],
        },
        headers=mini,
    ).json()
    types1 = {d["type"]: d["points"] for d in r1["points_detail"]}
    assert types1["words_convert"] == 4
    assert "quiz_full_marks" in types1 and types1["quiz_full_marks"] == 10
    assert "quiz_first_pass" not in types1
    # 第二本 560 词
    book2 = client.post(
        "/api/admin/books",
        json={"isbn": "9787100000014", "title": "B2", "word_count": 560},
        headers=h,
    ).json()
    for i in range(1, 6):
        client.post(
            f"/api/admin/books/{book2['id']}/questions",
            json={
                "question_type": "boolean",
                "question_text": f"Q{i}?",
                "options": ["对", "错"],
                "answer": "对",
            },
            headers=h,
        )
    import io

    mp3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
    client.post(
        f"/api/admin/books/{book2['id']}/audio",
        files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=h,
    )
    from backend.database import get_session
    from backend.domain.catalog.models import Book as BookModel

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book2["id"]).first()
        b.audio_duration_seconds = 600
        db.commit()
    client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book2["id"],
            "position": 10,
            "session_start": 0,
        },
        headers=mini,
    )
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        prog = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == c["id"], ReadingProgress.book_id == book2["id"])
            .first()
        )
        prog.last_report_at = datetime.now() - timedelta(seconds=590)
        db.commit()
    client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book2["id"],
            "position": 600,
            "session_start": 10,
        },
        headers=mini,
    )
    r2 = client.post(
        f"/api/miniapp/quiz/{book2['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["对", "对", "对", "对", "对"],
        },
        headers=mini,
    ).json()
    types2 = {d["type"]: d["points"] for d in r2["points_detail"]}
    # 50 + 560 = 610 → +6 分，余 10；第二本也满分 → +10（互斥不叠 +5）
    assert types2["words_convert"] == 6
    assert types2["quiz_full_marks"] == 10
    g = client.get(f"/api/admin/children/{c['id']}/growth", headers=h).json()
    assert g["summary"]["words_remainder"] == 10
    assert g["summary"]["points_total"] == 4 + 10 + 6 + 10


def test_level_up_and_recalc(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000705", "9787100000005", word_count=100)
    r = client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "对"],
        },
        headers=mini,
    ).json()
    assert r["level_up"] is False  # A 级起步，1 本不升级
    # admin 把阈值调成 1 → 重算 → 升 B
    client.put(
        "/api/admin/configs/level_up_books", json={"value": "1", "reason": "测试重算"}, headers=h
    )
    rc = client.post("/api/admin/growth/levels/recalc", headers=h)
    assert rc.status_code == 200
    assert rc.json()["level_changed"] == 1
    s = client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).json()
    assert s["level"] == "B"
    # 阈值调回 100 → 重算 → 只升不降，仍 B
    client.put(
        "/api/admin/configs/level_up_books", json={"value": "100", "reason": "调回"}, headers=h
    )
    client.post("/api/admin/growth/levels/recalc", headers=h)
    s2 = client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).json()
    assert s2["level"] == "B"


def test_milestone_award_and_manual_check(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000706", "9787100000006", word_count=2500)
    client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["A", "A", "A", "A", "对"],
        },
        headers=mini,
    )
    s = client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).json()
    assert s["milestones_awarded"] == []
    # admin 把 100000 节点调到 2500 以下 → 补发
    client.put(
        "/api/admin/configs/milestone_nodes",
        json={
            "value": "2000,100000,500000",
            "reason": "测试里程碑补发",
        },
        headers=h,
    )
    mc = client.post(f"/api/admin/children/{c['id']}/milestones/check", headers=h)
    assert mc.status_code == 200
    assert mc.json()["new_nodes"] == [2000]
    s2 = client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).json()
    assert s2["milestones_awarded"] == [2000]


def test_checkin_streak_points(client: TestClient):
    """连续打卡第 7 天 → +10（第 1 个 7 天周期）。"""
    from datetime import date
    from datetime import timedelta as td

    from backend.database import get_session
    from backend.domain.reading.models import CheckIn

    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000707", "9787100000007")
    # 手工造连续 6 天打卡，然后第 7 天走真实事件链（_checkin 发布事件 → growth 入账）
    # 先删掉 setup 完播 book1 产生的今日打卡（第 7 天的打卡必须由 book2 触发）
    with get_session() as db:
        db.query(CheckIn).filter(
            CheckIn.child_id == c["id"], CheckIn.checkin_date == date.today()
        ).delete()
        for i in range(6, 0, -1):
            db.add(
                CheckIn(
                    child_id=c["id"],
                    checkin_date=date.today() - td(days=i),
                    book_id=book["id"],
                    streak=7 - i,
                )
            )
        db.commit()
    # 第 7 天：真实完播打卡（换一本书触发当天首次打卡）
    book2 = client.post(
        "/api/admin/books",
        json={"isbn": "9787100000017", "title": "Streak", "word_count": 100},
        headers=h,
    ).json()
    import io

    mp3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
    client.post(
        f"/api/admin/books/{book2['id']}/audio",
        files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=h,
    )
    from backend.domain.catalog.models import Book as BookModel

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book2["id"]).first()
        b.audio_duration_seconds = 600
        db.commit()
    client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book2["id"],
            "position": 10,
            "session_start": 0,
        },
        headers=mini,
    )
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        prog = (
            db.query(ReadingProgress)
            .filter(ReadingProgress.child_id == c["id"], ReadingProgress.book_id == book2["id"])
            .first()
        )
        prog.last_report_at = datetime.now() - timedelta(seconds=590)
        db.commit()
    r = client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": c["id"],
            "book_id": book2["id"],
            "position": 600,
            "session_start": 10,
        },
        headers=mini,
    )
    assert r.json()["checkin"]["streak"] == 7
    pts = client.get(f"/api/miniapp/points?child_id={c['id']}", headers=mini).json()
    streak_pts = [p for p in pts if p["reason_type"] == "checkin_7"]
    assert len(streak_pts) == 1 and streak_pts[0]["points"] == 10


def test_points_manual_adjust(client: TestClient):
    h = _h(client)
    c, book, mini = _setup_finished_book(client, h, "13800000708", "9787100000008")
    r = client.post(
        f"/api/admin/children/{c['id']}/points/adjust",
        json={
            "points": 20,
            "reason": "线下活动奖励",
        },
        headers=h,
    )
    assert r.status_code == 200
    # 无原因被拒
    r2 = client.post(
        f"/api/admin/children/{c['id']}/points/adjust",
        json={
            "points": 5,
            "reason": "",
        },
        headers=h,
    )
    assert r2.status_code == 422
    s = client.get(f"/api/miniapp/growth/summary?child_id={c['id']}", headers=mini).json()
    assert s["points_total"] == 20
