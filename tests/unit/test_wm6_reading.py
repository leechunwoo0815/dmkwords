# tests/unit/test_wm6_reading.py — 阅读链（真实链路；防刷核心验证）
"""防刷协议（PRD R-151）：心跳每 10 秒；Δ覆盖 ≤ 服务端时间差×2.0×1.2+宽限(60s)。
测试用 _backdate 把 last_report_at 拨回 N 秒前，模拟真实时间流逝（覆盖增速与
墙上时钟物理一致 = 合法；不一致 = 拒绝）。"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _setup(client, h, phone="13800000601", isbn="9780545582889", duration=600):
    """会员孩子 + 押金 + 带音频的书（600 秒）+ 小程序登录 token。"""
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "阅读孩"}, headers=h
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
    book = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": "Dog Man", "word_count": 2500}, headers=h
    ).json()
    # 造 MP3（最小帧头）并上传 → duration 解析可能为 0，直接改库设时长
    import io

    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
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
    # 小程序登录
    r = client.post("/api/miniapp/login", json={"phone": phone, "code": "1234"})
    mini_h = {"Authorization": f"Bearer {r.json()['token']}"}
    return c, book, mini_h


def _report(client, mini, child_id, book_id, position, session_start):
    return client.post(
        "/api/miniapp/reading/progress",
        json={
            "child_id": child_id,
            "book_id": book_id,
            "position": position,
            "session_start": session_start,
        },
        headers=mini,
    )


def _backdate(client, child_id: int, book_id: int, seconds: int):
    """把 last_report_at 拨回 N 秒前（模拟真实时间流逝；防刷以服务端墙上时钟为基准）。"""
    from backend.database import get_session
    from backend.domain.reading.models import ReadingProgress

    with get_session() as db:
        p = (
            db.query(ReadingProgress)
            .filter(
                ReadingProgress.child_id == child_id,
                ReadingProgress.book_id == book_id,
                ReadingProgress.is_deleted == 0,
            )
            .first()
        )
        assert p is not None, "progress row must exist before backdating"
        p.last_report_at = datetime.now() - timedelta(seconds=seconds)
        db.commit()


def test_progress_and_finish(client: TestClient):
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000601")
    # 心跳：听 10 秒（首次上报，Δ10 ≤ 宽限 60 合法）
    r = _report(client, mini, c["id"], book["id"], 10, 0)
    assert r.status_code == 200
    # 时间流逝 290 秒后，续听到 300（50%）
    _backdate(client, c["id"], book["id"], 290)
    r = _report(client, mini, c["id"], book["id"], 300, 10)
    body = r.json()
    assert body["coverage_percent"] == 50.0
    assert body["finished"] is False
    # 再流逝 280 秒，续听到 580（96.7% ≥95% → 完播 + 打卡）
    _backdate(client, c["id"], book["id"], 280)
    r2 = _report(client, mini, c["id"], book["id"], 580, 300)
    body2 = r2.json()
    assert body2["finished"] is True
    assert body2["just_finished"] is True
    assert body2["checkin"]["checked_in"] is True
    assert body2["reading_minutes"] == 10  # 600 秒 = 10 分钟（原始时长）
    # 日历可见
    cal = client.get(f"/api/miniapp/checkins?child_id={c['id']}", headers=mini).json()
    assert cal["today_checked"] is True
    assert cal["current_streak"] == 1


def test_seek_jump_not_counted(client: TestClient):
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000602", "9783333333333")
    # 真实播放 0-60
    r1 = _report(client, mini, c["id"], book["id"], 60, 0)
    assert r1.status_code == 200
    # seek 拖到 590 续播 5 秒：客户端如实上报新区间 [590,595]（seek 段 [60,590] 不计入覆盖）
    _backdate(client, c["id"], book["id"], 5)
    r2 = _report(client, mini, c["id"], book["id"], 595, 590)
    assert r2.status_code == 200
    body = r2.json()
    assert body["coverage_seconds"] == 65  # 60 + 5，seek 段不计
    assert body["coverage_percent"] == 10.8  # 65/600
    assert body["finished"] is False


def test_speed_anomaly_rejected(client: TestClient):
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000603", "9784444444444")
    # 首次上报即声明 [0,590]：墙上时钟差=0，Δ覆盖 590 > 宽限 60（物理不可能）→ 拒绝
    r = _report(client, mini, c["id"], book["id"], 590, 0)
    assert r.status_code == 422
    assert "异常" in r.json()["detail"]
    # 拒绝后进度不落库（无脏数据）：再查进度为空
    view = client.get(
        f"/api/miniapp/books/{book['id']}/progress?child_id={c['id']}", headers=mini
    ).json()
    assert view["coverage_seconds"] == 0
    assert view["finished"] is False

    # 二段式刷：正常听 10 秒后，仅过 10 秒真实时间就声明覆盖到 590（Δ580 > 10×2.4+60=84）→ 拒
    r1 = _report(client, mini, c["id"], book["id"], 10, 0)
    assert r1.status_code == 200
    _backdate(client, c["id"], book["id"], 10)
    r2 = _report(client, mini, c["id"], book["id"], 590, 10)
    assert r2.status_code == 422
    assert "异常" in r2.json()["detail"]


def test_repeat_listen_no_double_trigger(client: TestClient):
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000604", "9785555555555")
    _report(client, mini, c["id"], book["id"], 10, 0)
    _backdate(client, c["id"], book["id"], 590)
    r = _report(client, mini, c["id"], book["id"], 600, 10)
    assert r.json()["finished"] is True
    assert r.json()["reading_minutes"] == 10
    # 重听（时间充足，合法）：just_finished=False、时长不重复、打卡不重复
    _backdate(client, c["id"], book["id"], 600)
    r2 = _report(client, mini, c["id"], book["id"], 600, 0)
    assert r2.json()["just_finished"] is False
    assert r2.json()["reading_minutes"] == 10
    cal = client.get(f"/api/miniapp/checkins?child_id={c['id']}", headers=mini).json()
    assert cal["current_streak"] == 1


def test_reservation_flow(client: TestClient):
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000605", "9786666666666")
    # 预约
    r = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r.status_code == 200
    rid = r.json()["id"]
    assert "expires_at" in r.json()
    # 重复预约拒
    r2 = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r2.status_code == 409
    # 取消
    r3 = client.post(
        f"/api/miniapp/reservations/{rid}/cancel", json={"child_id": c["id"]}, headers=mini
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "cancelled"
    # 取消后副本释放 → 可再次预约
    r4 = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert r4.status_code == 200
    # 列表两条（cancelled + active）
    mine = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()
    assert len(mine) == 2
    assert mine[0]["title"] == "Dog Man"


def test_miniapp_login_wrong_code(client: TestClient):
    r = client.post("/api/miniapp/login", json={"phone": "13800000606", "code": "9999"})
    assert r.status_code == 422


def _setup_book_with_audio(client, h, isbn, title="Dog Man", duration=600):
    book = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 2500}, headers=h
    ).json()
    import io

    mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 125000
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
    return book


def test_member_permission_for_playback(client: TestClient):
    """FEAT-038：未入会无播放权；过期仅能播在借的书。"""
    h = _h(client)
    # 未入会孩子 → 拒
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": "13800000607"}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "未入会孩"}, headers=h
    ).json()
    book = _setup_book_with_audio(client, h, "9787777777777", "No Member")
    r = client.post("/api/miniapp/login", json={"phone": "13800000607", "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    resp = _report(client, mini, c["id"], book["id"], 10, 0)
    assert resp.status_code == 422
    assert "入会" in resp.json()["detail"]

    # 有效会员 + 押金 + 在借一本书 → 过期后：在借书可播、其他书被拒
    c2, book2, mini2 = _setup(client, h, "13800000608", "9788888888888")
    br = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c2["id"], "isbn": "9788888888888"},
        headers=h,
    )
    assert br.status_code == 200, br.text
    from backend.database import get_session
    from backend.domain.identity.models import Child as ChildModel

    with get_session() as db:
        ch = db.query(ChildModel).filter(ChildModel.id == c2["id"]).first()
        ch.member_status = "expired"
        db.commit()
    ok = _report(client, mini2, c2["id"], book2["id"], 10, 0)
    assert ok.status_code == 200
    other = _setup_book_with_audio(client, h, "9789999999999", "Other Book")
    denied = _report(client, mini2, c2["id"], other["id"], 10, 0)
    assert denied.status_code == 422
    assert "在借" in denied.json()["detail"]


def test_admin_reservation_checkout_and_profile(client: TestClient):
    """管理端：预约列表 → 核销转借阅 → 孩子档案阅读数据（手册步骤 12-14）。"""
    h = _h(client)
    c, book, mini = _setup(client, h, "13800000609", "9781212121212")
    # 完播一本书（供档案展示）
    _report(client, mini, c["id"], book["id"], 10, 0)
    _backdate(client, c["id"], book["id"], 590)
    r = _report(client, mini, c["id"], book["id"], 600, 10)
    assert r.json()["finished"] is True
    # 预约
    res = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book["id"]}, headers=mini
    )
    assert res.status_code == 200
    rid = res.json()["id"]
    # 管理端列表（status=active）
    lst = client.get("/api/admin/reservations?status=active", headers=h).json()
    assert len(lst) == 1
    assert lst[0]["book_title"] == "Dog Man"
    assert lst[0]["child_name"] == "阅读孩"
    assert lst[0]["expired"] is False
    # 核销转借阅
    co = client.post(f"/api/admin/reservations/{rid}/checkout", headers=h)
    assert co.status_code == 200, co.text
    assert co.json()["reservation_id"] == rid
    assert co.json()["due_at"]
    # 预约变 checked_out
    mine = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()
    assert mine[0]["status"] == "checked_out"
    # 重复核销被拒
    co2 = client.post(f"/api/admin/reservations/{rid}/checkout", headers=h)
    assert co2.status_code == 422
    # 孩子档案阅读数据
    prof = client.get(f"/api/admin/children/{c['id']}/reading", headers=h).json()
    assert prof["total_finished"] == 1
    assert prof["total_reading_minutes"] == 10
    assert prof["total_checkin_days"] == 1
    assert prof["current_streak"] == 1
    assert prof["finished_books"][0]["title"] == "Dog Man"
