# tests/unit/test_wm6_parent_guard.py — P0-F1：reading 域 6 端点 child 归属校验
"""水平越权防护：家长 A 的 token 操作家长 B 的 child_id 必须被拒（修复前 200）。
_child_of_parent 抛 ValidationError（422）；不存在 child_id 走同一 422 路径（附带
bug：list_reservations 原 AttributeError 500）。"""

from fastapi.testclient import TestClient

from tests.unit.helpers import force_book_on


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, name="孩"):
    p = client.post(
        "/api/admin/members/parents", json={"name": f"家长{phone[-2:]}", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": name}, headers=h
    ).json()
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': phone, 'code': '1234'}).json()['token']}"
    }
    return p, c, mini


def _mk_book(client, h, title="归属测试书") -> int:
    import io

    r = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 5},
        headers=h,
    )
    assert r.status_code == 200, r.text
    book_id = r.json()["id"]
    force_book_on(client, h, book_id)  # 新书默认下架（D1），阅读链需上架书
    client.post(
        f"/api/admin/books/{book_id}/audio",
        files={"file": ("a.mp3", io.BytesIO(b"\xff\xfb\x90\x00" + b"\x00" * 125000), "audio/mpeg")},
        headers=h,
    )
    from backend.database import get_session
    from backend.domain.catalog.models import Book as BookModel

    with get_session() as db:
        b = db.query(BookModel).filter(BookModel.id == book_id).first()
        b.audio_duration_seconds = 600
        db.commit()
    return book_id


def test_cross_parent_access_rejected_on_all_six_endpoints(client: TestClient):
    """A 的 token 调 B 的 child_id：6 端点全部 422（修复前 200，水平越权）。"""
    h = _h(client)
    book_id = _mk_book(client, h)
    _, _, mini_a = _family(client, h, "13800002001", "A孩")
    _, child_b, _ = _family(client, h, "13800002002", "B孩")
    b_id = child_b["id"]

    # 1. GET /books/{id}/progress
    r1 = client.get(f"/api/miniapp/books/{book_id}/progress", params={"child_id": b_id}, headers=mini_a)
    assert r1.status_code == 422, f"progress GET 越权未拦: {r1.status_code} {r1.text}"
    # 2. POST /reading/progress
    r2 = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": b_id, "book_id": book_id, "position": 10},
        headers=mini_a,
    )
    assert r2.status_code == 422, f"progress POST 越权未拦: {r2.status_code} {r2.text}"
    # 3. GET /checkins
    r3 = client.get("/api/miniapp/checkins", params={"child_id": b_id}, headers=mini_a)
    assert r3.status_code == 422, f"checkins 越权未拦: {r3.status_code} {r3.text}"
    # 4. GET /reservations
    r4 = client.get("/api/miniapp/reservations", params={"child_id": b_id}, headers=mini_a)
    assert r4.status_code == 422, f"reservations GET 越权未拦: {r4.status_code} {r4.text}"
    # 5. POST /reservations
    r5 = client.post(
        "/api/miniapp/reservations", json={"child_id": b_id, "book_id": book_id}, headers=mini_a
    )
    assert r5.status_code == 422, f"reservations POST 越权未拦: {r5.status_code} {r5.text}"
    # 6. POST /reservations/{id}/cancel（目标单不存在也先在归属校验被拦）
    r6 = client.post(
        "/api/miniapp/reservations/999/cancel", json={"child_id": b_id}, headers=mini_a
    )
    assert r6.status_code == 422, f"reservations cancel 越权未拦: {r6.status_code} {r6.text}"


def test_own_child_still_works(client: TestClient):
    """防误伤：A 的 token 调 A 自己的 child_id 全部正常 200。"""
    h = _h(client)
    book_id = _mk_book(client, h, "自孩书")
    _, child_a, mini_a = _family(client, h, "13800002003", "自孩")
    a_id = child_a["id"]
    # 入会（R-313 音频门禁：progress 需会员）：观察期订单 + 确认收款（真实链路）
    o = client.post(
        "/api/admin/orders", json={"child_id": a_id, "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    # 押金（预约前提：deposit paid）
    do = client.post(f"/api/admin/deposits/children/{a_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )

    r1 = client.get(f"/api/miniapp/books/{book_id}/progress", params={"child_id": a_id}, headers=mini_a)
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/miniapp/reading/progress",
        json={"child_id": a_id, "book_id": book_id, "position": 5},
        headers=mini_a,
    )
    assert r2.status_code == 200, r2.text
    r3 = client.get("/api/miniapp/checkins", params={"child_id": a_id}, headers=mini_a)
    assert r3.status_code == 200, r3.text
    r4 = client.get("/api/miniapp/reservations", params={"child_id": a_id}, headers=mini_a)
    assert r4.status_code == 200, r4.text
    r5 = client.post(
        "/api/miniapp/reservations", json={"child_id": a_id, "book_id": book_id}, headers=mini_a
    )
    assert r5.status_code == 200, r5.text
    rid = r5.json()["id"]
    r6 = client.post(
        f"/api/miniapp/reservations/{rid}/cancel", json={"child_id": a_id}, headers=mini_a
    )
    assert r6.status_code == 200, r6.text


def test_nonexistent_child_422_not_500(client: TestClient):
    """附带 bug：child_id 不存在时统一 422（原 list_reservations AttributeError 500）。"""
    h = _h(client)
    _, _, mini_a = _family(client, h, "13800002004", "存在孩")
    r = client.get("/api/miniapp/reservations", params={"child_id": 99999}, headers=mini_a)
    assert r.status_code == 422, f"应为 422 而非 {r.status_code}: {r.text[:120]}"
