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
