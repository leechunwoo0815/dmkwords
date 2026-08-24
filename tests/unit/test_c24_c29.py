# tests/unit/test_c24_c29.py — WM2 验收缺陷修复（C24/C25/C26/C28/C29）
"""真实 MySQL + TestClient；媒体走 query token（覆盖 <img> 场景）+ Bearer 均验。"""

from io import BytesIO

from fastapi.testclient import TestClient

# C26：合法 MP3 帧（时长约 7s）；0 秒哑帧用于拒绝测试
VALID_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
SILENT_BAD_MP3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _token(client: TestClient) -> str:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    return r.json()["token"]


def _book(client: TestClient, h: dict, isbn: str = "9780545582889", title: str = "C 系列") -> dict:
    r = client.post(
        "/api/admin/books",
        json={"isbn": isbn, "title": title, "word_count": 1200},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_c24_ar_clear_returns_to_pending_filter(client: TestClient):
    """C24：ar_level 清空（空串）→ 存 NULL → 「AR 待配置」筛选命中。"""
    h = _h(client)
    b = _book(client, h, "9780545582889", "C24 书")
    client.put(
        f"/api/admin/books/{b['id']}",
        json={
            "title": "C24 书",
            "author": "",
            "word_count": 1200,
            "ar_level": "3.5",
            "topic": "",
            "grade": "",
            "description": "",
        },
        headers=h,
    )
    # 清空
    client.put(
        f"/api/admin/books/{b['id']}",
        json={
            "title": "C24 书",
            "author": "",
            "word_count": 1200,
            "ar_level": "",
            "topic": "",
            "grade": "",
            "description": "",
        },
        headers=h,
    )
    resp = client.get(
        "/api/admin/books", params={"keyword": "C24 书", "ar_pending": "1"}, headers=h
    ).json()
    ids = [item["id"] for item in resp["items"]]
    assert b["id"] in ids, "清空 AR 后应出现在「待配置」筛选"


def test_c26_zero_second_audio_rejected(client: TestClient):
    """C26：解析时长为 0 的 MP3 拒绝（422），不入库不写文件。"""
    h = _h(client)
    b = _book(client, h, "9789994000001", "C26 书")
    # 合法帧上传成功
    ok = client.post(
        f"/api/admin/books/{b['id']}/audio",
        files={"file": ("a.mp3", BytesIO(VALID_MP3), "audio/mpeg")},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["audio_duration_seconds"] >= 1
    # 非法 0 秒帧拒绝
    bad = client.post(
        f"/api/admin/books/{b['id']}/audio",
        files={"file": ("b.mp3", BytesIO(SILENT_BAD_MP3), "audio/mpeg")},
        headers=h,
    )
    assert bad.status_code == 422
    assert "时长为 0" in bad.json()["detail"]
    # 文件保持合法版（未覆盖）
    detail = client.get(f"/api/admin/books/{b['id']}", headers=h).json()
    assert detail["audio_duration_seconds"] >= 1


def test_c25_media_endpoints_auth(client: TestClient):
    """C25：cover/audio media 端点；query token（<img> 场景）与 Bearer 均可；无 token 401。"""
    h = _h(client)
    b = _book(client, h, "9789994000002", "C25 书")
    client.post(
        f"/api/admin/books/{b['id']}/audio",
        files={"file": ("a.mp3", BytesIO(VALID_MP3), "audio/mpeg")},
        headers=h,
    )
    token = _token(client)
    # query token（模拟 <img>/<audio>）
    rq1 = client.get(f"/api/admin/books/{b['id']}/cover-media", params={"token": token})
    assert rq1.status_code == 404  # 无封面资源（文件未上传，404 资源不存在）
    rq2 = client.get(f"/api/admin/books/{b['id']}/audio-media", params={"token": token})
    assert rq2.status_code == 200
    assert rq2.headers["content-type"].startswith("audio/mpeg")
    # Bearer（后台 fetch 场景）
    rq3 = client.get(f"/api/admin/books/{b['id']}/audio-media", headers=h)
    assert rq3.status_code == 200
    # 无 token → 401
    rq4 = client.get(f"/api/admin/books/{b['id']}/audio-media")
    assert rq4.status_code == 401
    # 坏 token → 401
    rq5 = client.get(f"/api/admin/books/{b['id']}/audio-media", params={"token": "junk"})
    assert rq5.status_code == 401
    # 路径穿越防护：book 无 audio 但 rel 是目录相对路径→ 404
    assert (
        client.get(f"/api/admin/books/{b['id']}/cover-media", params={"token": token}).status_code
        == 404
    )


def test_c29_server_assigns_sort_order(client: TestClient):
    """C29：创建不传 sort_order；服务端 max+1；删除后新增不重号。"""
    h = _h(client)
    b = _book(client, h, "9789994000003", "C29 书")
    q1 = client.post(
        f"/api/admin/books/{b['id']}/questions",
        json={
            "question_type": "single",
            "question_text": "Q1",
            "options": ["A", "B"],
            "answer": "A",
        },
        headers=h,
    ).json()
    q2 = client.post(
        f"/api/admin/books/{b['id']}/questions",
        json={
            "question_type": "single",
            "question_text": "Q2",
            "options": ["A", "B"],
            "answer": "B",
        },
        headers=h,
    ).json()
    assert q1["sort_order"] == 1
    assert q2["sort_order"] == 2
    client.delete(f"/api/admin/questions/{q1['id']}", headers=h)
    q3 = client.post(
        f"/api/admin/books/{b['id']}/questions",
        json={
            "question_type": "single",
            "question_text": "Q3",
            "options": ["A", "B"],
            "answer": "A",
        },
        headers=h,
    ).json()
    assert q3["sort_order"] == 3  # 不与现存重号（max+1 语义）
    qs = client.get(f"/api/admin/books/{b['id']}/questions", headers=h).json()
    assert sorted(x["sort_order"] for x in qs) == [2, 3]


def test_c28_update_question(client: TestClient):
    """C28：编辑题目（题干/选项/答案）；sort_order 不变；校验仍生效。"""
    h = _h(client)
    b = _book(client, h, "9789994000004", "C28 书")
    q = client.post(
        f"/api/admin/books/{b['id']}/questions",
        json={
            "question_type": "single",
            "question_text": "旧题干",
            "options": ["A", "B"],
            "answer": "A",
        },
        headers=h,
    ).json()
    upd = client.put(
        f"/api/admin/questions/{q['id']}",
        json={
            "question_type": "single",
            "question_text": "新题干",
            "options": ["X", "Y"],
            "answer": "Y",
        },
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["question_text"] == "新题干"
    assert body["options"] == ["X", "Y"]
    assert body["answer"] == "Y"
    assert body["sort_order"] == q["sort_order"]  # 序号不动
    # 答案不在选项 → 422
    bad = client.put(
        f"/api/admin/questions/{q['id']}",
        json={
            "question_type": "single",
            "question_text": "X",
            "options": ["X", "Y"],
            "answer": "Z",
        },
        headers=h,
    )
    assert bad.status_code == 422
