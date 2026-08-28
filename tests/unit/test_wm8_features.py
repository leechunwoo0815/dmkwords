# tests/unit/test_wm8_features.py — 榜单/护照/报告/生词本/收藏夹（真实链路）
import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_child(client, h, phone, name, english_name=None, member=True, deposit=True):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children",
        json={"name": name, "english_name": english_name},
        headers=h,
    ).json()
    if member:
        o = client.post(
            "/api/admin/orders",
            json={"child_id": c["id"], "order_type": "observation_fee"},
            headers=h,
        ).json()
        client.post(
            f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
        )
    if deposit:
        do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
        client.post(
            f"/api/admin/orders/{do['order_id']}/confirm-payment",
            json={"pay_method": "scan"},
            headers=h,
        )
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    return c, mini


def _credit_words(client, h, child_id, isbn, words, created_at=None):
    """直接入账词数（走 WordsLedger 唯一约束：一书一次；可回溯时间）。"""
    from backend.database import get_session
    from backend.domain.catalog.models import Book
    from backend.domain.growth.models import WordsLedger

    with get_session() as db:
        book = db.query(Book).filter(Book.isbn == isbn).first()
        if not book:
            raise AssertionError(f"book {isbn} not found")
        db.add(
            WordsLedger(
                child_id=child_id,
                book_id=book.id,
                word_count=words,
                created_at=created_at or datetime.now(),
            )
        )
        db.commit()


def _mk_book(client, h, isbn, word_count=1000):
    return client.post(
        "/api/admin/books",
        json={
            "isbn": isbn,
            "title": f"B{isbn[-3:]}",
            "word_count": word_count,
        },
        headers=h,
    ).json()


def test_leaderboard_week_and_privacy(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_child(client, h, "13800000801", "小明", english_name="Ming")
    c2, m2 = _mk_child(client, h, "13800000802", "小红", english_name="Hong")
    _mk_book(client, h, "9788200000001", 3000)
    _mk_book(client, h, "9788200000002", 1500)
    _credit_words(client, h, c1["id"], "9788200000001", 3000)
    _credit_words(client, h, c2["id"], "9788200000002", 1500)
    # 上周的词不计入周榜
    _mk_book(client, h, "9788200000003", 9000)
    _credit_words(
        client, h, c1["id"], "9788200000003", 9000, created_at=datetime.now() - timedelta(days=10)
    )

    board = client.get(
        "/api/miniapp/leaderboard?period=week&child_id=" + str(c1["id"]), headers=m1
    ).json()
    assert board["title"] == "本周词数榜"
    names = [e["name"] for e in board["entries"]]
    assert names[0] == "Ming" and names[1] == "Hong"
    assert board["entries"][0]["words"] == 3000  # 上周 9000 不计
    assert board["my_rank"] == 1
    # 隐私：无手机号无全名
    body = str(board)
    assert "13800000801" not in body and "小明" not in body


def test_leaderboard_total_with_history_student(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_child(client, h, "13800000803", "阿退", english_name="Retired")
    c2, m2 = _mk_child(client, h, "13800000804", "阿在", english_name="Active")
    _mk_book(client, h, "9788200000011", 5000)
    _mk_book(client, h, "9788200000012", 1000)
    _credit_words(client, h, c1["id"], "9788200000011", 5000)
    _credit_words(client, h, c2["id"], "9788200000012", 1000)
    # c1 退会
    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        ch = db.query(Child).filter(Child.id == c1["id"]).first()
        ch.member_status = "withdrawn"
        db.commit()
    # 总榜：退会孩子保留，标历史学员
    board = client.get(
        "/api/miniapp/leaderboard?period=total&child_id=" + str(c2["id"]), headers=m2
    ).json()
    top = board["entries"][0]
    assert top["name"] == "Retired" and top["is_history"] is True
    # 周榜：退会孩子不上
    week = client.get(
        "/api/miniapp/leaderboard?period=week&child_id=" + str(c2["id"]), headers=m2
    ).json()
    week_names = [e["name"] for e in week["entries"]]
    assert "Retired" not in week_names


def test_leaderboard_progress_min_increment(client: TestClient):
    h = _h(client)
    c1, m1 = _mk_child(client, h, "13800000805", "进步孩", english_name="Grow")
    _mk_book(client, h, "9788200000021", 300)
    _mk_book(client, h, "9788200000022", 50)
    # 本周 +300，上周 +0 → 增量 300 ≥ 100 上榜
    _credit_words(client, h, c1["id"], "9788200000021", 300)
    b = client.get(
        "/api/miniapp/leaderboard?period=progress&child_id=" + str(c1["id"]), headers=m1
    ).json()
    assert b["entries"][0]["words"] == 300
    # 另一孩子只增 50 → 不上榜
    c2, m2 = _mk_child(client, h, "13800000806", "小步孩", english_name="Small")
    _credit_words(client, h, c2["id"], "9788200000022", 50)
    b2 = client.get(
        "/api/miniapp/leaderboard?period=progress&child_id=" + str(c1["id"]), headers=m1
    ).json()
    names = [e["name"] for e in b2["entries"]]
    assert "Small" not in names


def test_leaderboard_requires_membership(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13800000807", "未入会", member=False, deposit=False)
    r = client.get("/api/miniapp/leaderboard?period=week&child_id=" + str(c["id"]), headers=m)
    assert r.status_code == 422
    assert "入会" in r.json()["detail"]


def test_passport(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13800000808", "护照孩", english_name="Pass")
    _mk_book(client, h, "9788200000031", 2500)
    _credit_words(client, h, c["id"], "9788200000031", 2500)
    p = client.get(f"/api/miniapp/passport?child_id={c['id']}", headers=m).json()
    assert p["words_total"] == 2500
    assert p["english_name"] == "Pass"
    assert p["read_only"] is False
    assert p["recent_books"][0]["word_count"] == 2500
    assert "level" in p and "points_total" in p


def test_report_image_generation(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13800000809", "报告孩")
    _mk_book(client, h, "9788200000041", 1800)
    # 周报统计上个自然周 → 入账时间回填上周
    _credit_words(
        client, h, c["id"], "9788200000041", 1800, created_at=datetime.now() - timedelta(days=7)
    )
    # 管理端生成周报图片
    r = client.post(f"/api/admin/children/{c['id']}/reports/weekly/generate", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"].startswith("reports/")
    assert body["url"].startswith("/api/admin/uploads/reports/")
    assert body["data"]["words"] == 1800
    # 文件真实存在且是 PNG
    from backend.config import get_settings

    full = os.path.join(get_settings().UPLOADS_DIR, body["path"])
    assert os.path.isfile(full)
    with open(full, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
    # 小程序数据接口
    data = client.get(f"/api/miniapp/reports/weekly?child_id={c['id']}", headers=m).json()
    assert data["words"] == 1800
    assert "image_url" in data


def test_vocabulary_lookup_and_unique(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13800000810", "查词孩")
    book = _mk_book(client, h, "9788200000051")
    # 查 adventure（手册验收词）
    r = client.get(
        f"/api/miniapp/vocabulary/lookup?word=adventure&child_id={c['id']}&book_id={book['id']}",
        headers=m,
    ).json()
    assert r["word"] == "adventure"
    assert "冒险" in r["translation"]
    assert r["recorded"] is True
    # 重复查：不重复收录
    r2 = client.get(
        f"/api/miniapp/vocabulary/lookup?word=adventure&child_id={c['id']}", headers=m
    ).json()
    assert r2["recorded"] is False
    lst = client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=m).json()
    assert len(lst) == 1
    assert lst[0]["source_title"] == "B051"
    # 查不存在词
    r3 = client.get(f"/api/miniapp/vocabulary/lookup?word=zzzzzz&child_id={c['id']}", headers=m)
    assert r3.status_code == 404
    # 非法输入
    r4 = client.get(f"/api/miniapp/vocabulary/lookup?word=abc123&child_id={c['id']}", headers=m)
    assert r4.status_code == 422
    # 删除
    r5 = client.delete(f"/api/miniapp/vocabulary/{lst[0]['id']}?child_id={c['id']}", headers=m)
    assert r5.status_code == 200
    lst2 = client.get(f"/api/miniapp/vocabulary?child_id={c['id']}", headers=m).json()
    assert len(lst2) == 0


def test_favorites_flow(client: TestClient):
    h = _h(client)
    # 未入会也可收藏
    c, m = _mk_child(client, h, "13800000811", "收藏孩", member=False, deposit=False)
    book = _mk_book(client, h, "9788200000061")
    r = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book["id"]}, headers=m
    )
    assert r.status_code == 200
    # 重复收藏被拒
    r2 = client.post(
        "/api/miniapp/favorites", json={"child_id": c["id"], "book_id": book["id"]}, headers=m
    )
    assert r2.status_code == 409
    # 下架书仍可见但标注（D1：先 force_on 再下架）
    from tests.unit.helpers import force_book_on

    force_book_on(client, h, book["id"])
    client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    lst = client.get(f"/api/miniapp/favorites?child_id={c['id']}", headers=m).json()
    assert lst[0]["off_shelf"] is True
    # 取消
    r3 = client.delete(f"/api/miniapp/favorites/{book['id']}?child_id={c['id']}", headers=m)
    assert r3.status_code == 200
    lst2 = client.get(f"/api/miniapp/favorites?child_id={c['id']}", headers=m).json()
    assert len(lst2) == 0


def test_shelf_borrows(client: TestClient):
    h = _h(client)
    c, m = _mk_child(client, h, "13800000812", "书架孩")
    _mk_book(client, h, "9788200000071")
    # 借书前为空
    lst = client.get(f"/api/miniapp/borrows?child_id={c['id']}", headers=m).json()
    assert len(lst) == 0
    br = client.post(
        "/api/admin/circulation/borrow",
        json={
            "child_id": c["id"],
            "isbn": "9788200000071",
        },
        headers=h,
    )
    assert br.status_code == 200, br.text
    lst2 = client.get(f"/api/miniapp/borrows?child_id={c['id']}", headers=m).json()
    assert len(lst2) == 1
    assert lst2[0]["title"] == "B071"
    assert lst2[0]["overdue"] is False
    # 还书后下架
    copy_id = br.json()["copy_id"]
    client.post(
        "/api/admin/circulation/return", json={"copy_id": copy_id, "condition": "normal"}, headers=h
    )
    lst3 = client.get(f"/api/miniapp/borrows?child_id={c['id']}", headers=m).json()
    assert len(lst3) == 0
