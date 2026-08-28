# tests/unit/test_wm2_p2.py — WM2 P2 体验优化（P2-5/7/8/10/12 + C2/C3）
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.domain.catalog.repository import BookCopyRepository


def _h(client, username="admin") -> dict:
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_book(client, h, **over) -> dict:
    body = {"isbn": None, "title": "P2 Book", "word_count": 100, "copy_count": 1}
    body.update(over)
    resp = client.post("/api/admin/books", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _jpg_bytes(color="red") -> bytes:
    img = Image.new("RGB", (10, 10), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------- P2-5 测验双口径 ----------


def test_p2_5_question_dual_counts(client: TestClient):
    """5 题（3 启用 2 停用）→ question_count=5 / question_active_count=3；书出现在 quiz_incomplete Tab。"""
    h = _h(client)
    book = _create_book(client, h, title="Dual Count")
    for i in range(5):
        resp = client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i}",
                "options": ["A", "B"],
                "answer": "A",
            },
            headers=h,
        )
        assert resp.status_code == 200
    questions = client.get(f"/api/admin/books/{book['id']}/questions", headers=h).json()
    for q in questions[:2]:
        r = client.post(f"/api/admin/questions/{q['id']}/toggle-active", headers=h)
        assert r.status_code == 200

    items = client.get("/api/admin/books", headers=h).json()["items"]
    row = next(b for b in items if b["id"] == book["id"])
    assert row["question_count"] == 5
    assert row["question_active_count"] == 3

    detail = client.get(f"/api/admin/books/{book['id']}", headers=h).json()
    assert detail["question_active_count"] == 3

    # 启用数 3 < 5 → 应命中 quiz_incomplete Tab
    tab_ids = [
        b["id"]
        for b in client.get("/api/admin/books", params={"quiz_incomplete": True}, headers=h).json()[
            "items"
        ]
    ]
    assert book["id"] in tab_ids


# ---------- P2-7 表格排序 ----------


def test_p2_7_sort_word_count_asc(client: TestClient):
    h = _h(client)
    b1 = _create_book(client, h, title="W300", word_count=300)
    b2 = _create_book(client, h, title="W100", word_count=100)
    b3 = _create_book(client, h, title="W200", word_count=200)
    resp = client.get("/api/admin/books", params={"sort": "word_count", "order": "asc"}, headers=h)
    ids = [b["id"] for b in resp.json()["items"]]
    assert ids == [b2["id"], b3["id"], b1["id"]]


def test_p2_7_sort_copy_count_desc(client: TestClient):
    h = _h(client)
    b1 = _create_book(client, h, title="C1", copy_count=1)
    b2 = _create_book(client, h, title="C3", copy_count=3)
    b3 = _create_book(client, h, title="C2", copy_count=2)
    resp = client.get("/api/admin/books", params={"sort": "copy_count", "order": "desc"}, headers=h)
    ids = [b["id"] for b in resp.json()["items"]]
    assert ids == [b2["id"], b3["id"], b1["id"]]


def test_p2_7_invalid_sort_falls_back_to_default(client: TestClient):
    """非法 sort 静默回默认 id desc（不 422）。"""
    h = _h(client)
    _create_book(client, h, title="D1")
    b2 = _create_book(client, h, title="D2")
    resp = client.get(
        "/api/admin/books", params={"sort": "evil_field;--", "order": "asc"}, headers=h
    )
    assert resp.status_code == 200
    ids = [b["id"] for b in resp.json()["items"]]
    assert ids == sorted(ids, reverse=True)  # id desc
    assert b2["id"] == ids[0]


# ---------- P2-8 ISBN 自动清洗 ----------


def test_p2_8_create_isbn_cleaned(client: TestClient):
    h = _h(client)
    resp = client.post(
        "/api/admin/books",
        json={"isbn": "978-0-5455-8288-9", "title": "Clean ISBN", "word_count": 100},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["isbn"] == "9780545582889"


def test_p2_8_import_isbn_cleaned(client: TestClient):
    from openpyxl import Workbook

    h = _h(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"])
    ws.append(["978-0-5455-8288-9", "清洗导入", "", "3.5", "100", "", "", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("t.xlsx", buf, "application/vnd.ms-excel")},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["success_count"] == 1, resp.json()
    book = client.get("/api/admin/books", params={"keyword": "清洗导入"}, headers=h).json()[
        "items"
    ][0]
    assert book["isbn"] == "9780545582889"


# ---------- P2-10 并发加副本撞唯一索引 ----------


def test_p2_10_add_copies_conflict_409(client: TestClient, monkeypatch):
    """撞 uq_copy_code → 409 ConflictError（非裸 500），detail 含「副本编码冲突」。"""
    h = _h(client)
    book = _create_book(client, h, title="Conflict Copies", copy_count=1)
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    existing_code = copies[0]["copy_code"]

    monkeypatch.setattr(
        BookCopyRepository, "next_copy_code", lambda self, book, seq=None: existing_code
    )
    resp = client.post(f"/api/admin/books/{book['id']}/copies?count=1", headers=h)
    assert resp.status_code == 409, resp.text
    assert "副本编码冲突" in resp.json()["detail"]


# ---------- P2-12 词数最小 1 ----------


@pytest.mark.parametrize("path", ["create", "update"])
def test_p2_12_word_count_min_1(client: TestClient, path: str):
    h = _h(client)
    if path == "create":
        resp = client.post(
            "/api/admin/books", json={"isbn": None, "title": "W0", "word_count": 0}, headers=h
        )
    else:
        book = _create_book(client, h, title="W0 Upd")
        resp = client.put(
            f"/api/admin/books/{book['id']}",
            json={"isbn": None, "title": "W0 Upd", "word_count": 0},
            headers=h,
        )
    assert resp.status_code == 422


def test_p2_12_import_word_count_0_rejected(client: TestClient):
    from openpyxl import Workbook

    h = _h(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名*", "作者", "AR值", "词数*", "主题", "适读阶段", "副本数"])
    ws.append([None, "零词数", "", "", "0", "", "", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("t.xlsx", buf, "application/vnd.ms-excel")},
        headers=h,
    )
    data = resp.json()
    assert data["failed_count"] == 1
    assert "总词数必须是正整数" in data["errors"][0]


# ---------- C2 借书报错中文状态 ----------


def _member_with_book(client, h, phone="13800000901", name="C2孩"):
    """观察期会员 + 押金 + 一本书（复用 wm5 测试基建模式）。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    book = _create_book(client, h, title="C2 Borrow Book", isbn="9780545582889")
    return c, book


def test_c2_borrow_reserved_copy_chinese_error(client: TestClient):
    """reserved 副本按副本ID借书 → 409 detail 含「预约锁定」，不含英文 "reserved"。"""
    h = _h(client)
    c, book = _member_with_book(client, h)
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    r = client.put(
        f"/api/admin/copies/{copies[0]['id']}/status",
        json={"status": "reserved", "reason": "测试预约锁定"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    resp = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": copies[0]["id"]},
        headers=h,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "预约锁定" in detail
    assert "reserved" not in detail


# ---------- C3 Tab 计数 ----------


def test_c3_tab_counts_exact(client: TestClient):
    """counts 7 键与各口径精确匹配；下架一本 → on-1/off+1。（D1：新书默认下架入库）"""
    from tests.unit.helpers import force_book_on

    h = _h(client)
    b1 = _create_book(client, h, title="C3-1", ar_level="3.5")
    _create_book(client, h, title="C3-2", ar_level="3.5")
    _create_book(client, h, title="C3-3")
    _create_book(client, h, title="C3-4")
    _create_book(client, h, title="C3-5")
    # b1 传封面 → no_cover 剩 4
    up = client.post(
        f"/api/admin/books/{b1['id']}/cover",
        files={"file": ("c.jpg", _jpg_bytes(), "image/jpeg")},
        headers=h,
    )
    assert up.status_code == 200
    # 上架 b1-b4（force 过完整性）→ on 4 / off 1
    by_title = {
        b["title"]: b["id"] for b in client.get("/api/admin/books", headers=h).json()["items"]
    }
    for t in ["C3-1", "C3-2", "C3-3", "C3-4"]:
        r = force_book_on(client, h, by_title[t])
        assert r.status_code == 200

    data = client.get("/api/admin/books", headers=h).json()
    counts = data["counts"]
    assert counts == {
        "all": 5,
        "on": 4,
        "off": 1,
        "ar": 3,
        "no_cover": 4,
        "no_audio": 5,
        "quiz_incomplete": 5,
    }, counts

    # 再下架 C3-3（on→off 方向不校验）→ on-1/off+1
    client.post(f"/api/admin/books/{by_title['C3-3']}/toggle-status", headers=h)
    counts2 = client.get("/api/admin/books", headers=h).json()["counts"]
    assert counts2["on"] == counts["on"] - 1
    assert counts2["off"] == counts["off"] + 1
