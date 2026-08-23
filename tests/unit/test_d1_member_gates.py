# tests/unit/test_d1_member_gates.py — P0 修复回归（docs/09：D1/C13/C15/C16/C17）
"""覆盖：
- D1 会员过期边界：formal 到期未落库 → 借书软拦截可放行 / 续借硬拒 / 预约拒 / 音频仅在手 /
  周期榜剔除 + 总榜历史标签 / 二孩折扣不触发；
- C13 观察期→待评估：馆员标记按钮 + 评估通过转正（创建年费订单→收款→formal）；
- C15 未入会放行借书：限 1 本 + 72 小时借期 + 归还后释放；
- C16 借书 AR 超范围软提示（不拦截）。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, child_name="孩子"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    return p, c


def _pay_deposit(client, h, child_id):
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )


def _formal_child(client, h, phone, name="正式孩"):
    """建档 → 年费收款 → formal（到期日 = 今天 + 365）。"""
    p, c = _family(client, h, phone, name)
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return c


def _expire_member(db, child_id, days=1):
    """把 formal 孩子的到期日拨回 N 天前（状态保持 formal，模拟定时任务未落库）。"""
    from backend.domain.identity.models import Child

    ch = db.query(Child).filter(Child.id == child_id).first()
    assert ch is not None and ch.member_status == "formal"
    ch.member_expire = date.today() - timedelta(days=days)
    db.commit()
    return ch


def _book(client, h, isbn, title="测试书", ar_level=None, word_count=1000):
    body = {"isbn": isbn, "title": title, "word_count": word_count}
    if ar_level is not None:
        body["ar_level"] = ar_level
    return client.post("/api/admin/books", json=body, headers=h).json()


def _borrow(client, h, child_id, isbn, reason=None):
    body = {"child_id": child_id, "isbn": isbn}
    if reason:
        body["override_reason"] = reason
    return client.post("/api/admin/circulation/borrow", json=body, headers=h)


# ==================== D1：过期边界（借/续/约/听/榜/折扣） ====================


def test_expired_formal_borrow_soft_override(client: TestClient, db):
    """过期借书：无放行硬拒；馆员放行可借（软提示，不吃未入会开关）。"""
    h = _h(client)
    c = _formal_child(client, h, "13800000901", "过期借书孩")
    _pay_deposit(client, h, c["id"])
    _expire_member(db, c["id"])
    book = _book(client, h, "9789100000001", "过期可借书")
    # 开关保持关闭：过期路径不吃未入会开关
    r = _borrow(client, h, c["id"], book["isbn"])
    assert r.status_code == 422
    assert "已过期" in r.json()["detail"]
    # 馆员放行 → 借出 + warning 留痕
    r2 = _borrow(client, h, c["id"], book["isbn"], reason="家长答应本周续费")
    assert r2.status_code == 200, r2.text
    assert any("已过期" in w for w in r2.json()["warnings"])


def test_expired_formal_renew_blocked(client: TestClient, db):
    """过期续借：硬拒（D3/R-313 自助续借行，无可放行口径）。"""
    h = _h(client)
    c = _formal_child(client, h, "13800000902", "过期续借孩")
    _pay_deposit(client, h, c["id"])
    book = _book(client, h, "9789100000002", "过期续借书")
    record = _borrow(client, h, c["id"], book["isbn"]).json()
    _expire_member(db, c["id"])
    r = client.post("/api/admin/circulation/renew", json={"record_id": record["id"]}, headers=h)
    assert r.status_code == 422
    assert "已过期" in r.json()["detail"]


def test_expired_formal_reservation_blocked(client: TestClient, db):
    """过期预约：拒（D3）。"""
    h = _h(client)
    c = _formal_child(client, h, "13800000903", "过期预约孩")
    _pay_deposit(client, h, c["id"])
    _expire_member(db, c["id"])
    book = _book(client, h, "9789100000003", "过期预约书")
    r = client.post("/api/miniapp/login", json={"phone": "13800000903", "code": "1234"})
    mini = {"Authorization": f"Bearer {r.json()['token']}"}
    res = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": book["id"]},
        headers=mini,
    )
    assert res.status_code == 422
    assert "有效会员" in res.json()["detail"]


def test_expired_formal_audio_only_holding(client: TestClient, db):
    """过期音频：仅已借未还可播；其他书被拒（D1 涟漪：formal 未落库也走过期分支）。"""
    h = _h(client)
    c = _formal_child(client, h, "13800000904", "过期听书孩")
    _pay_deposit(client, h, c["id"])

    def _audio_book(isbn, title):
        import io

        book = _book(client, h, isbn, title)
        mp3 = b"\xff\xfb\x90\x64" + b"\x00" * 2000
        client.post(
            f"/api/admin/books/{book['id']}/audio",
            files={"file": ("a.mp3", io.BytesIO(mp3), "audio/mpeg")},
            headers=h,
        )
        from backend.domain.catalog.models import Book as BookModel

        b = db.query(BookModel).filter(BookModel.id == book["id"]).first()
        b.audio_duration_seconds = 600
        db.commit()
        return book

    held = _audio_book("9789100000004", "在手书")
    other = _audio_book("9789100000005", "非在手书")
    assert _borrow(client, h, c["id"], held["isbn"]).status_code == 200
    _expire_member(db, c["id"])
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000904', 'code': '1234'}).json()['token']}"
    }

    def _report(book_id, position=10):
        return client.post(
            "/api/miniapp/reading/progress",
            json={
                "child_id": c["id"],
                "book_id": book_id,
                "position": position,
                "session_start": 0,
            },
            headers=mini,
        )

    assert _report(held["id"]).status_code == 200
    denied = _report(other["id"])
    assert denied.status_code == 422
    assert "在借" in denied.json()["detail"]


def test_weekly_board_excludes_expired_formal(client: TestClient, db):
    """周期榜剔除过期 formal；总榜仍展示且标记历史（R-317 + D1）。"""
    h = _h(client)
    active = _formal_child(client, h, "13800000905", "在榜孩")
    expired = _formal_child(client, h, "13800000906", "掉榜孩")
    book = _book(client, h, "9789100000006", "榜单书")
    from backend.domain.growth.models import WordsLedger

    for child_id in (active["id"], expired["id"]):
        db.add(WordsLedger(child_id=child_id, book_id=book["id"], word_count=100))
    db.commit()
    _expire_member(db, expired["id"])
    mini = {
        "Authorization": f"Bearer {client.post('/api/miniapp/login', json={'phone': '13800000905', 'code': '1234'}).json()['token']}"
    }
    week = client.get(
        "/api/miniapp/leaderboard",
        params={"period": "week", "child_id": active["id"]},
        headers=mini,
    ).json()
    week_ids = [e["child_id"] for e in week["entries"]]
    assert active["id"] in week_ids
    assert expired["id"] not in week_ids
    total = client.get(
        "/api/miniapp/leaderboard",
        params={"period": "total", "child_id": active["id"]},
        headers=mini,
    ).json()
    total_entries = {e["child_id"]: e for e in total["entries"]}
    assert expired["id"] in total_entries
    assert total_entries[expired["id"]]["is_history"] is True


def test_expired_sibling_no_second_child_discount(client: TestClient, db):
    """过期的 formal 哥哥不触发二孩 9 折（D1：折扣判活用日期感知口径）。"""
    h = _h(client)
    p, c1 = _family(client, h, "13800000907", "过期哥")
    o = client.post(
        "/api/admin/orders", json={"child_id": c1["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    _expire_member(db, c1["id"])
    c2 = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": "二孩"}, headers=h
    ).json()
    o2 = client.post(
        "/api/admin/orders", json={"child_id": c2["id"], "order_type": "formal_fee"}, headers=h
    ).json()
    assert Decimal(o2["amount"]) == Decimal("6000")


# ==================== C13：观察期 → 待评估 → 转正 ====================


def _observation_child(client, h, phone, name="观察孩"):
    p, c = _family(client, h, phone, name)
    o = client.post(
        "/api/admin/orders", json={"child_id": c["id"], "order_type": "observation_fee"}, headers=h
    ).json()
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return c


def test_mark_pending_evaluation_and_audit(client: TestClient):
    """C13：观察期 → 馆员标记待评估（留痕）；非观察期被拒。"""
    h = _h(client)
    c = _observation_child(client, h, "13800000908", "待评估孩")
    r = client.post(
        f"/api/admin/members/children/{c['id']}/mark-pending-evaluation",
        json={"reason": "观察期一个月已到，约家长到店评估"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["member_status"] == "pending_evaluation"
    logs = client.get(
        "/api/admin/audit-logs", params={"action": "child.mark_pending_evaluation"}, headers=h
    ).json()
    assert logs["total"] >= 1
    assert logs["items"][0]["reason"] == "观察期一个月已到，约家长到店评估"
    # 重复标记（已是待评估）→ 拒
    r2 = client.post(
        f"/api/admin/members/children/{c['id']}/mark-pending-evaluation",
        json={"reason": "再标一次"},
        headers=h,
    )
    assert r2.status_code == 422


def test_evaluate_approve_flow(client: TestClient):
    """C13/R-101-5：评估通过转正 = 创建年费订单 → 收款确认 → formal。"""
    h = _h(client)
    c = _observation_child(client, h, "13800000909", "转正孩")
    client.post(
        f"/api/admin/members/children/{c['id']}/mark-pending-evaluation",
        json={"reason": "评估通过"},
        headers=h,
    )
    r = client.post(
        f"/api/admin/members/children/{c['id']}/evaluate-approve",
        json={"reason": "听力达标，同意转正"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["order_type"] == "formal_fee"
    assert order["status"] == "pending_manual_confirm"
    assert Decimal(order["amount"]) == Decimal("6000")
    logs = client.get(
        "/api/admin/audit-logs", params={"action": "child.evaluate_approve"}, headers=h
    ).json()
    assert logs["total"] >= 1
    # 收款确认 → 正式会员
    paid = client.post(
        f"/api/admin/orders/{order['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    assert paid.status_code == 200
    child = next(
        x
        for x in client.get("/api/admin/members/children", headers=h).json()["items"]
        if x["id"] == c["id"]
    )
    assert child["member_status"] == "formal"


# ==================== C15：未入会放行借书限 1 本 / 72h ====================


def test_unpaid_override_one_book_72h_and_release(client: TestClient):
    """C15/R-313：开关开启 + 放行 → 每次限 1 本、借期 72 小时；归还后释放。"""
    h = _h(client)
    # 开关默认关 → 先开启
    sw = client.put(
        "/api/admin/configs/allow_unpaid_offline_borrow",
        json={"value": "true", "reason": "测试未入会放行"},
        headers=h,
    )
    assert sw.status_code == 200
    p, c = _family(client, h, "13800000910", "未入会放行孩")
    _pay_deposit(client, h, c["id"])  # 押金缴清以隔离会员变量
    b1 = _book(client, h, "9789100010001", "放行书一")
    b2 = _book(client, h, "9789100010002", "放行书二")
    # 第一本：放行借出，借期 72 小时
    r1 = _borrow(client, h, c["id"], b1["isbn"], reason="先借回家试读，周末办入会")
    assert r1.status_code == 200, r1.text
    rec = r1.json()
    assert any("未入会" in w for w in rec["warnings"])
    hours = (
        datetime.fromisoformat(rec["due_at"]) - datetime.fromisoformat(rec["borrowed_at"])
    ).total_seconds() / 3600
    assert 71.9 < hours <= 72.1, f"未入会放行借期应为 72 小时，实际 {hours}"
    # 第二本：限 1 本 → 拒（放行也不行）
    r2 = _borrow(client, h, c["id"], b2["isbn"], reason="还想再借一本")
    assert r2.status_code == 422
    assert "限 1 本" in r2.json()["detail"]
    # 归还第一本 → 释放，可再借
    rr = client.post(
        "/api/admin/circulation/return",
        json={"copy_id": rec["copy_id"], "condition": "normal"},
        headers=h,
    )
    assert rr.status_code == 200
    r3 = _borrow(client, h, c["id"], b2["isbn"], reason="已还，再借一本")
    assert r3.status_code == 200, r3.text


# ==================== C16：AR 超范围软提示 ====================


def test_ar_range_warning_not_blocking(client: TestClient, db):
    """C16/FEAT-031：AR 差值超阈值 → warning 不拦截；阈值内无提示。"""
    h = _h(client)
    c = _observation_child(client, h, "13800000911", "AR孩")
    _pay_deposit(client, h, c["id"])
    from backend.domain.identity.models import Child

    ch = db.query(Child).filter(Child.id == c["id"]).first()
    ch.ar_level = "2.0"
    db.commit()
    far = _book(client, h, "9789100010003", "AR差距书", ar_level="3.0")
    near = _book(client, h, "9789100010004", "AR接近书", ar_level="2.2")
    r1 = _borrow(client, h, c["id"], far["isbn"])
    assert r1.status_code == 200, r1.text
    assert any("AR 超范围" in w for w in r1.json()["warnings"])
    r2 = _borrow(client, h, c["id"], near["isbn"])
    assert r2.status_code == 200, r2.text
    assert not any("AR 超范围" in w for w in r2.json()["warnings"])
