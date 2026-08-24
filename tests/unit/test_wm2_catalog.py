# tests/unit/test_wm2_catalog.py — 图书资产（真实链路）
import io

from fastapi.testclient import TestClient
from openpyxl import Workbook


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_book(client, h, **over) -> dict:
    body = {"isbn": None, "title": "Test Book", "word_count": 1000, "copy_count": 1, "author": "A"}
    body.update(over)
    resp = client.post("/api/admin/books", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_create_book_with_isbn(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="9780545582889", title="Dog Man", word_count=2500)
    assert book["isbn"] == "9780545582889"
    assert book["status"] == 1
    assert book["copy_count"] == 1


def test_create_book_without_isbn_gets_internal_code(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, title="无ISBN书")
    assert book["isbn"] is None
    assert book["internal_code"].startswith("LOCAL-")


def test_duplicate_isbn_rejected(client: TestClient):
    h = _h(client)
    _create_book(client, h, isbn="1111111111111", title="A")
    resp = client.post(
        "/api/admin/books",
        json={"isbn": "1111111111111", "title": "B", "word_count": 10, "copy_count": 1},
        headers=h,
    )
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]


def test_bad_isbn_rejected(client: TestClient):
    h = _h(client)
    resp = client.post(
        "/api/admin/books",
        json={"isbn": "abc", "title": "X", "word_count": 10, "copy_count": 1},
        headers=h,
    )
    assert resp.status_code == 422


def test_ar_pending_filter(client: TestClient):
    h = _h(client)
    _create_book(client, h, isbn="2222222222222", title="No AR")
    _create_book(client, h, isbn="3333333333333", title="Has AR", ar_level="2.5")
    resp = client.get("/api/admin/books", params={"ar_pending": True}, headers=h)
    titles = [b["title"] for b in resp.json()["items"]]
    assert "No AR" in titles
    assert "Has AR" not in titles


def test_toggle_status_and_audit(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="4444444444444", title="Toggle")
    resp = client.post(f"/api/admin/books/{book['id']}/toggle-status", headers=h)
    assert resp.json()["status"] == 0
    resp = client.get("/api/admin/books", params={"status": 0}, headers=h)
    assert any(b["title"] == "Toggle" for b in resp.json()["items"])


def test_copy_status_matrix(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="5555555555555", title="Matrix")
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    copy_id = copies[0]["id"]
    # available -> maintenance OK
    r = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "maintenance", "reason": "破损"},
        headers=h,
    )
    assert r.status_code == 200
    # maintenance -> borrowed 非法（矩阵拦截）
    r = client.put(
        f"/api/admin/copies/{copy_id}/status", json={"status": "borrowed", "reason": "x"}, headers=h
    )
    assert r.status_code == 422
    assert "转移矩阵" in r.json()["detail"]
    # maintenance -> available（修复）-> lost -> available（找回）
    client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "available", "reason": "修好"},
        headers=h,
    )
    client.put(
        f"/api/admin/copies/{copy_id}/status", json={"status": "lost", "reason": "丢失"}, headers=h
    )
    r = client.put(
        f"/api/admin/copies/{copy_id}/status",
        json={"status": "available", "reason": "找回"},
        headers=h,
    )
    assert r.status_code == 200


def test_add_copies(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="6666666666666", title="Copies")
    r = client.post(f"/api/admin/books/{book['id']}/copies", params={"count": 2}, headers=h)
    assert len(r.json()) == 2
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()
    assert len(copies) == 3


def test_excel_import_with_error_rows(client: TestClient):
    h = _h(client)
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名", "作者", "AR值", "总词数", "主题", "年级", "副本数量"])
    ws.append(["7777777777777", "Import A", "Auth", "1.5", 800, "绘本", "一年级", 1])
    ws.append(["bad-isbn", "Import B", "", "", 100, "", "", 1])  # 错误行
    ws.append(["8888888888888", "Import C", "", "", 500, "", "", 2])
    ws.append([None, None, None, None, None, None, None, None])  # 空行跳过
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={
            "file": (
                "books.xlsx",
                buf,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=h,
    )
    body = resp.json()
    assert body["success_count"] == 2
    assert body["failed_count"] == 1
    assert "第2行" in body["errors"][0]


def test_excel_import_same_isbn_adds_copies(client: TestClient):
    h = _h(client)
    _create_book(client, h, isbn="9999999999999", title="Existing")
    wb = Workbook()
    ws = wb.active
    ws.append(["ISBN", "书名", "作者", "AR值", "总词数", "主题", "年级", "副本数量"])
    ws.append(["9999999999999", "Existing", "", "", 100, "", "", 2])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = client.post(
        "/api/admin/books/import",
        files={"file": ("books.xlsx", buf, "application/octet-stream")},
        headers=h,
    )
    assert resp.json()["success_count"] == 1
    books = client.get("/api/admin/books", params={"keyword": "Existing"}, headers=h).json()[
        "items"
    ]
    assert books[0]["copy_count"] == 3  # 1 + 2


def test_cover_upload_converts_to_jpg(client: TestClient):
    from PIL import Image

    h = _h(client)
    book = _create_book(client, h, isbn="1212121212121", title="Cover")
    img = Image.new("RGB", (60, 90), color=(44, 74, 110))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    resp = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("cover.png", buf, "image/png")},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["cover_path"].startswith("cover/1212/")
    assert resp.json()["cover_path"].endswith(".jpg")


def test_audio_upload_rejects_non_mp3(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="1313131313131", title="Audio")
    resp = client.post(
        f"/api/admin/books/{book['id']}/audio",
        files={"file": ("song.wav", b"RIFF....", "audio/wav")},
        headers=h,
    )
    assert resp.status_code == 422
    assert "MP3" in resp.json()["detail"]


def test_quiz_question_crud_and_validation(client: TestClient):
    h = _h(client)
    book = _create_book(client, h, isbn="1414141414141", title="Quiz")
    q = {
        "question_type": "single",
        "question_text": "Who is the hero?",
        "options": ["Dog Man", "Cat Kid", "Petey", "Lil Petey"],
        "answer": "Dog Man",
    }
    resp = client.post(f"/api/admin/books/{book['id']}/questions", json=q, headers=h)
    assert resp.status_code == 200
    qid = resp.json()["id"]
    # 答案不在选项中 → 拒绝
    bad = dict(q, answer="Nobody")
    resp = client.post(f"/api/admin/books/{book['id']}/questions", json=bad, headers=h)
    assert resp.status_code == 422
    # 判断题选项固定
    resp = client.post(
        f"/api/admin/books/{book['id']}/questions",
        json={
            "question_type": "boolean",
            "question_text": "T?",
            "options": ["Yes", "No"],
            "answer": "Yes",
        },
        headers=h,
    )
    assert resp.status_code == 422
    # 列表 + 停用
    questions = client.get(f"/api/admin/books/{book['id']}/questions", headers=h).json()
    assert len(questions) == 1
    resp = client.post(f"/api/admin/questions/{qid}/toggle-active", headers=h)
    assert resp.json()["is_active"] == 0


def test_staff_can_manage_books(client: TestClient):
    h = _h(client, "staff01")
    resp = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "By Staff", "word_count": 100, "copy_count": 1},
        headers=h,
    )
    assert resp.status_code == 200
