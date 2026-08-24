# tests/unit/test_audit_fixes.py — WM1-WM10 自审回归（音频流/报告图片/书目详情/押金退款拦截）
import io

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_child_with_audio_book(client, h, phone, isbn):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "审查孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    book = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": "Audit Book", "word_count": 800}, headers=h
    ).json()
    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
    client.post(
        f"/api/admin/books/{book['id']}/audio",
        files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
        headers=h,
    )
    # 哑 MP3 解析不出时长 → 直接改库（与其他用例同口径）
    from backend.database import get_session
    from backend.domain.catalog.models import Book as BookModel

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book["id"]).first()
        b.audio_duration_seconds = 600
        db.commit()
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    return c, book, mini, r.json()["token"]


def test_miniapp_book_detail_endpoint(client: TestClient):
    """书架（收藏/在借）进入详情时缺 audio_url —— 详情接口补全。"""
    h = _h(client)
    c, book, mini, token = _mk_child_with_audio_book(client, h, "13800002001", "9782000000001")
    r = client.get(f"/api/miniapp/books/{book['id']}", headers=mini)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_audio"] is True
    assert body["audio_url"] == f"/api/miniapp/books/{book['id']}/audio"
    assert body["audio_duration"] is not None
    # 不存在的书
    r404 = client.get("/api/miniapp/books/99999", headers=mini)
    assert r404.status_code == 404


def test_audio_stream_endpoint(client: TestClient):
    """音频流：query token 鉴权 + audio/mpeg（此前端点缺失 → 播放器 404）。"""
    h = _h(client)
    c, book, mini, token = _mk_child_with_audio_book(client, h, "13800002002", "9782000000002")
    url = f"/api/miniapp/books/{book['id']}/audio?token={token}"
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert len(r.content) > 1000
    # 无 token 拒
    r_noauth = client.get(f"/api/miniapp/books/{book['id']}/audio")
    assert r_noauth.status_code == 401
    # 坏 token 拒
    r_bad = client.get(f"/api/miniapp/books/{book['id']}/audio?token=bad.token.here")
    assert r_bad.status_code == 401


def test_report_image_endpoint(client: TestClient):
    """周报图片：query token（此前 _parent_from_token 缺失 → 500）。"""
    h = _h(client)
    c, book, mini, token = _mk_child_with_audio_book(client, h, "13800002003", "9782000000003")
    # 完播 + 过测验入账词数（供周报统计）
    from datetime import datetime, timedelta

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
    for i in range(1, 6):
        client.post(
            f"/api/admin/books/{book['id']}/questions",
            json={
                "question_type": "boolean",
                "question_text": f"Q{i}?",
                "options": ["对", "错"],
                "answer": "对",
            },
            headers=h,
        )
    client.post(
        f"/api/miniapp/quiz/{book['id']}/submit",
        json={
            "child_id": c["id"],
            "answers": ["对", "对", "对", "对", "对"],
        },
        headers=mini,
    )
    # 生成图片（管理端）→ 小程序 query-token 读取
    gen = client.post(f"/api/admin/children/{c['id']}/reports/weekly/generate", headers=h)
    assert gen.status_code == 200
    path = gen.json()["path"]
    # 图片接口（query token）能取回 PNG
    r = client.get(f"/api/miniapp/reports/weekly/image?child_id={c['id']}&token={token}")
    assert r.status_code == 200, r.text
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert path  # 管理端与小程序共用同一文件


def test_deposit_order_refund_blocked(client: TestClient):
    """押金退款不能单独申请（V1.1 §3.5）。"""
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800002004"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "押金孩"}, headers=h
    ).json()
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    do = client.post(f"/api/admin/deposits/children/{c['id']}/orders", headers=h).json()
    dep_order = client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )
    assert dep_order.status_code == 200
    r = client.post("/api/miniapp/login", json={"phone": "13800002004", "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    # 预览被拒
    pv = client.get(
        f"/api/miniapp/refund-preview?child_id={c['id']}&order_id={do['order_id']}", headers=mini
    )
    assert pv.status_code == 422
    assert "押金" in pv.json()["detail"]
    # 申请被拒
    ap = client.post(
        "/api/miniapp/refund-requests",
        json={
            "child_id": c["id"],
            "order_id": do["order_id"],
            "reason": "想退押金",
        },
        headers=mini,
    )
    assert ap.status_code == 422
    # 普通订单退款不受影响
    pv2 = client.get(
        f"/api/miniapp/refund-preview?child_id={c['id']}&order_id={o['id']}", headers=mini
    )
    assert pv2.status_code == 200


def test_observation_image_endpoint(client: TestClient):
    """评估报告图片：小程序 query-token 可访问，且不能越权读 observation/ 之外的文件。"""
    h = _h(client)
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800002005"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "图孩"}, headers=h
    ).json()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    up = client.post(
        f"/api/admin/children/{c['id']}/observation-reports",
        data={"remark": "审查"},
        files=[("files", ("r1.png", io.BytesIO(png), "image/png"))],
        headers=h,
    )
    assert up.status_code == 200
    rel = up.json()["images"][0]  # observation/child_X/xxx.png
    r = client.post("/api/miniapp/login", json={"phone": "13800002005", "code": "1234"})
    token = r.json()["token"]
    sub = rel.replace("observation/", "", 1)
    ok = client.get(f"/api/miniapp/observation-images/{sub}?token={token}")
    assert ok.status_code == 200, ok.text
    assert ok.content[:8] == b"\x89PNG\r\n\x1a\n"
    # 越权路径（跳出 observation/ 目录）被拒
    evil = client.get(f"/api/miniapp/observation-images/..%2F..%2Fetc%2Fpasswd?token={token}")
    assert evil.status_code in (404, 400)
    # 无 token 拒
    noauth = client.get(f"/api/miniapp/observation-images/{sub}")
    assert noauth.status_code == 401
