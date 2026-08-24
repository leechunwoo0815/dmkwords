# tests/unit/test_c31_c34.py — WM2 复测增强（C31-C34 后端契约）
import io

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_book(client, h, **over) -> dict:
    body = {"isbn": None, "title": "Test Book", "word_count": 1000, "copy_count": 1, "author": "A"}
    body.update(over)
    resp = client.post("/api/admin/books", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_update_book_add_isbn(client: TestClient):
    """C34：无 ISBN 书目可后补真实 ISBN，原内部编号保留。"""
    h = _h(client)
    book = _create_book(client, h, title="No ISBN Yet")
    assert book["isbn"] is None
    internal = book["internal_code"]
    resp = client.put(
        f"/api/admin/books/{book['id']}",
        json={
            "isbn": "9780545582889",
            "title": "No ISBN Yet",
            "author": "A",
            "word_count": 1000,
            "ar_level": None,
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["isbn"] == "9780545582889"
    assert data["internal_code"] == internal
    assert data["question_count"] == 0


def test_update_book_isbn_unique_and_format(client: TestClient):
    """C34：ISBN 后补/修改需校验唯一性与格式。"""
    h = _h(client)
    _create_book(client, h, isbn="9781111111111", title="B1")
    b2 = _create_book(client, h, title="B2")
    # 与 b1 重复
    resp = client.put(
        f"/api/admin/books/{b2['id']}",
        json={
            "isbn": "9781111111111",
            "title": "B2",
            "author": "A",
            "word_count": 1000,
            "ar_level": None,
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    assert resp.status_code == 409
    # 格式错误
    resp = client.put(
        f"/api/admin/books/{b2['id']}",
        json={
            "isbn": "bad-isbn",
            "title": "B2",
            "author": "A",
            "word_count": 1000,
            "ar_level": None,
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    assert resp.status_code == 422


def test_search_by_internal_code_and_isbn(client: TestClient):
    """C33：搜索支持内部编号与 ISBN。"""
    h = _h(client)
    book = _create_book(client, h, title="Searchable")
    internal = book["internal_code"]
    # 按内部编号搜
    resp = client.get("/api/admin/books", params={"keyword": internal}, headers=h)
    assert resp.status_code == 200
    assert any(b["id"] == book["id"] for b in resp.json()["items"])
    # 后补 ISBN 再按 ISBN 搜
    client.put(
        f"/api/admin/books/{book['id']}",
        json={
            "isbn": "9782222222222",
            "title": "Searchable",
            "author": "A",
            "word_count": 1000,
            "ar_level": None,
            "topic": "",
            "grade": "",
            "description": None,
        },
        headers=h,
    )
    resp = client.get("/api/admin/books", params={"keyword": "9782222222222"}, headers=h)
    assert any(b["id"] == book["id"] for b in resp.json()["items"])


def test_list_and_detail_include_question_count(client: TestClient):
    """C32：列表与详情返回测验题数量。"""
    h = _h(client)
    book = _create_book(client, h, title="With Questions")
    for i in range(2):
        resp = client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "single",
                "question_text": f"Q{i}?",
                "options": ["A", "B", "C", "D"],
                "answer": "A",
            },
            headers=h,
        )
        assert resp.status_code == 200
    resp = client.get("/api/admin/books", headers=h)
    item = next(b for b in resp.json()["items"] if b["id"] == book["id"])
    assert item["question_count"] == 2
    resp = client.get(f"/api/admin/books/{book['id']}", headers=h)
    assert resp.json()["question_count"] == 2


def test_delete_book_success(client: TestClient):
    """C32/C35：书目可删除，删除后列表不再出现，且 uploads 中媒体文件被清理。"""
    import os

    from PIL import Image

    from backend.config import get_settings

    h = _h(client)
    book = _create_book(client, h, title="To Delete")

    # 上传封面
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("x.jpg", buf, "image/jpeg")},
        headers=h,
    )
    assert resp.status_code == 200
    cover_path = resp.json()["cover_path"]

    # 上传音频
    audio = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    resp = client.post(
        f"/api/admin/books/{book['id']}/audio",
        files={"file": ("a.mp3", io.BytesIO(audio), "audio/mpeg")},
        headers=h,
    )
    assert resp.status_code == 200
    audio_path = resp.json()["audio_path"]

    root = os.path.abspath(get_settings().UPLOADS_DIR)
    cover_full = os.path.abspath(os.path.join(root, cover_path))
    audio_full = os.path.abspath(os.path.join(root, audio_path))
    assert os.path.isfile(cover_full)
    assert os.path.isfile(audio_full)

    # 删除书目
    resp = client.delete(f"/api/admin/books/{book['id']}", headers=h)
    assert resp.status_code == 200
    resp = client.get("/api/admin/books", headers=h)
    assert not any(b["id"] == book["id"] for b in resp.json()["items"])

    # 媒体文件应被清理
    assert not os.path.isfile(cover_full)
    assert not os.path.isfile(audio_full)


def test_cover_media_endpoint_returns_file(client: TestClient):
    """C31/C25：封面媒体端点可正常返回图片（大图预览前置条件）。"""
    from PIL import Image

    h = _h(client)
    book = _create_book(client, h, title="Cover Media")
    # 生成一张真实 10x10 RGB 图并转为 JPG
    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    resp = client.post(
        f"/api/admin/books/{book['id']}/cover",
        files={"file": ("x.jpg", buf, "image/jpeg")},
        headers=h,
    )
    assert resp.status_code == 200
    resp = client.get(
        f"/api/admin/books/{book['id']}/cover-media?token={h['Authorization'].split()[1]}"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"


def test_batch_delete_books(client: TestClient):
    """F2：批量删除书目；成功后列表不再出现，失败项返回原因。"""
    h = _h(client)
    b1 = _create_book(client, h, title="Batch A")
    b2 = _create_book(client, h, title="Batch B")
    resp = client.post(
        "/api/admin/books/batch-delete",
        json={"ids": [b1["id"], b2["id"]]},
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] == 2, data
    assert data["failed"] == 0, data
    resp = client.get("/api/admin/books", headers=h)
    ids = {b["id"] for b in resp.json()["items"]}
    assert b1["id"] not in ids
    assert b2["id"] not in ids


def test_filter_no_cover_no_audio_quiz_incomplete(client: TestClient):
    """F3：列表支持未传封面/未传音频/测验未录满 5 道筛选。"""
    h = _h(client)
    b_cover = _create_book(client, h, title="Has Cover")
    b_audio = _create_book(client, h, title="Has Audio")
    b_quiz = _create_book(client, h, title="Has Quiz")
    b_empty = _create_book(client, h, title="Empty Book")

    # 给 b_cover 传封面
    from PIL import Image

    img = Image.new("RGB", (10, 10), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    assert (
        client.post(
            f"/api/admin/books/{b_cover['id']}/cover",
            files={"file": ("x.jpg", buf, "image/jpeg")},
            headers=h,
        ).status_code
        == 200
    )

    # 给 b_audio 传音频
    audio = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    assert (
        client.post(
            f"/api/admin/books/{b_audio['id']}/audio",
            files={"file": ("a.mp3", io.BytesIO(audio), "audio/mpeg")},
            headers=h,
        ).status_code
        == 200
    )

    # 给 b_quiz 录 5 道题
    for i in range(5):
        assert (
            client.post(
                f"/api/admin/books/{b_quiz['id']}/questions",
                json={
                    "question_type": "single",
                    "question_text": f"Q{i}?",
                    "options": ["A", "B", "C", "D"],
                    "answer": "A",
                },
                headers=h,
            ).status_code
            == 200
        )

    # 未传封面筛选
    resp = client.get("/api/admin/books", params={"no_cover": True}, headers=h)
    ids = {b["id"] for b in resp.json()["items"]}
    assert b_empty["id"] in ids
    assert b_audio["id"] in ids
    assert b_quiz["id"] in ids
    assert b_cover["id"] not in ids

    # 未传音频筛选
    resp = client.get("/api/admin/books", params={"no_audio": True}, headers=h)
    ids = {b["id"] for b in resp.json()["items"]}
    assert b_empty["id"] in ids
    assert b_cover["id"] in ids
    assert b_quiz["id"] in ids
    assert b_audio["id"] not in ids

    # 测验未满 5 道筛选
    resp = client.get("/api/admin/books", params={"quiz_incomplete": True}, headers=h)
    ids = {b["id"] for b in resp.json()["items"]}
    assert b_empty["id"] in ids
    assert b_cover["id"] in ids
    assert b_audio["id"] in ids
    assert b_quiz["id"] not in ids
