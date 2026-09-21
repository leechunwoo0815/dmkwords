# tests/unit/test_borrow_scan.py — 借阅台扫码统一判定（2026-09-21 C 批，任务包 A–D）
"""`POST /api/admin/circulation/scan` 的判定顺序就是产品口径，逐条锁死：

① 扫会员码 → 回孩子卡片（馆员扫错框也能就地纠正）
②/③ 扫 ISBN：**已借出去的就是还书**（同一本书第二次扫自动变成还书）
④ **自己预约的可以直接借**（走预约核销；此前借书端点对 reserved 一律 409，本轮打通）
⑤ 在馆副本 → 标准借书链
⑥ 别人借出 / 别人预约锁定 → 中文原因拦截（含**谁的**、**什么时候到期**）
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _book(client: TestClient, h: dict, isbn: str, title: str) -> int:
    """建一本**有 ISBN** 的书（扫码链路必须按 ISBN 定位，故不能用 _book_with_copies 的无码书）。"""
    r = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 100}, headers=h
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _scan(client: TestClient, h: dict, child_id: int, code: str):
    return client.post(
        "/api/admin/circulation/scan", json={"child_id": child_id, "code": code}, headers=h
    )


def _ready_child(client: TestClient, h: dict, phone: str, name: str):
    _p, c, mini = _family(client, h, phone, name)
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    return c, mini


def _member_code(client: TestClient, h: dict, child_id: int) -> str:
    rows = client.get("/api/admin/members/children?page=1&page_size=50", headers=h).json()
    return {r["id"]: r.get("member_code") for r in rows["items"]}[child_id]


def test_scan_member_code_returns_card(client: TestClient):
    """扫会员码 → action=member + 卡片（前端据此建立"当前读者"会话）。"""
    h = _h(client)
    c, _mini = _ready_child(client, h, "13981015001", "扫会员孩")
    r = _scan(client, h, c["id"], _member_code(client, h, c["id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "member"
    assert body["card"] and body["card"]["child_id"] == c["id"]


def test_scan_book_borrow_then_return(client: TestClient):
    """同一个码扫两次：**第一次借出、第二次还书**（"已借出去的，扫码就是还书"）。"""
    h = _h(client)
    c, _mini = _ready_child(client, h, "13981015002", "扫码借还孩")
    _book(client, h, "9780394800101", "扫码借还书")

    r1 = _scan(client, h, c["id"], "9780394800101")
    assert r1.status_code == 200, r1.text
    assert r1.json()["action"] == "borrow"
    assert r1.json()["copy_id"] and r1.json()["due_at"]

    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 1

    r2 = _scan(client, h, c["id"], "9780394800101")
    assert r2.status_code == 200, r2.text
    assert r2.json()["action"] == "return"
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 0


def test_scan_own_reservation_checkout(client: TestClient):
    """**自己预约的书，扫码直接借出**（核销预约 → 借出；本轮打通的关键口径）。"""
    h = _h(client)
    c, mini = _ready_child(client, h, "13981015003", "自己预约孩")
    book_id = _book(client, h, "9780394800102", "自己预约书")
    rr = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book_id}, headers=mini
    )
    assert rr.status_code == 200, rr.text
    reservation_id = rr.json()["id"]

    r = _scan(client, h, c["id"], "9780394800102")
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "checkout"

    from backend.domain.catalog.models import BookCopy
    from backend.domain.reading.models import Reservation

    with _db() as db:
        res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
        assert res.status == Reservation.STATUS_CHECKED_OUT, "预约应被核销"
        copy = db.query(BookCopy).filter(BookCopy.book_id == book_id).first()
        assert copy.status == BookCopy.STATUS_BORROWED, "副本应转为借出"
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 1


def test_scan_book_reserved_by_other_blocked(client: TestClient):
    """别人预约锁定的书 → 拦截，且说清**谁**预约、**保留到什么时候**。"""
    h = _h(client)
    owner, owner_mini = _ready_child(client, h, "13981015004", "预约主人")
    other, _other_mini = _ready_child(client, h, "13981015005", "后来的人")
    book_id = _book(client, h, "9780394800103", "被预约书")
    client.post(
        "/api/miniapp/reservations",
        json={"child_id": owner["id"], "book_id": book_id},
        headers=owner_mini,
    )

    r = _scan(client, h, other["id"], "9780394800103")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "预约主人" in detail and "预约锁定" in detail, detail


def test_scan_book_borrowed_by_other_blocked(client: TestClient):
    """别人借出的书 → 拦截，且说清**谁借着**、**应还日期**。"""
    h = _h(client)
    holder, _holder_mini = _ready_child(client, h, "13981015006", "先借的人")
    other, _other_mini = _ready_child(client, h, "13981015007", "后到的人")
    _book(client, h, "9780394800104", "被借出书")
    assert _scan(client, h, holder["id"], "9780394800104").json()["action"] == "borrow"

    r = _scan(client, h, other["id"], "9780394800104")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "先借的人" in detail and "应还" in detail, detail


def test_scan_unknown_isbn(client: TestClient):
    """没录入的书 → 404（提示去哪儿建档，而不是"未知错误"）。"""
    h = _h(client)
    c, _mini = _ready_child(client, h, "13981015008", "扫空孩")
    r = _scan(client, h, c["id"], "9780000000000")
    assert r.status_code == 404, r.text
    assert "未入库" in r.json()["detail"]


def test_scan_mistyped_member_code_says_invalid(client: TestClient):
    """形似会员码但校验位不过 → 说"码不合法"，不是"ISBN 未入库"（实测踩过的误导文案）。"""
    h = _h(client)
    c, _mini = _ready_child(client, h, "13981015009", "错码孩")
    code = _member_code(client, h, c["id"])
    bad = code[:-1] + ("X" if code[-1] != "X" else "Y")
    r = _scan(client, h, c["id"], bad)
    assert r.status_code == 422, r.text
    assert "会员码不合法" in r.json()["detail"]


def test_borrow_releases_active_reservation_and_ghost_lock(client: TestClient):
    """借到书＝预约目的达成：在用预约必须转「已借出」，它锁着的**另一册**要放回在馆。

    用户 2026-09-21 反馈："预约的书如果已经成功借阅了，就应该释放预约，改成已借阅"。
    这里造的就是当时现场的形状：同一本书两册，孩子预约锁了 A 册，却从 B 册借走了书
    （扫码时 A 被跳过、B 被挑中）——若不处理，那条预约会永远占额度，A 册也永远 Borrowed 不了。
    """
    h = _h(client)
    c, mini = _ready_child(client, h, "13981015010", "预约释放孩")
    book_id = _book(client, h, "9780394800301", "预约释放书")
    # 再造一册，供"被借走的那册"与"被预约锁住的那册"分开
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        db.add(BookCopy(book_id=book_id, copy_code="RESREL-2", status=BookCopy.STATUS_AVAILABLE))
        db.commit()

    rr = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book_id}, headers=mini
    )
    assert rr.status_code == 200, rr.text
    reservation_id = rr.json()["id"]

    # 走**手动借出**（老「借出」按钮那条路）：取在馆那册，预约锁的那册被跳过 →
    # 这正是"预约没被核销、书却已经借到手"的形状（扫码会走核销，故此处不能用扫码复现）。
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "isbn": "9780394800301"},
        headers=h,
    )
    assert r.status_code == 200, r.text

    from backend.domain.reading.models import Reservation

    with _db() as db:
        res = db.query(Reservation).filter(Reservation.id == reservation_id).first()
        assert res.status == Reservation.STATUS_CHECKED_OUT, "借到书后预约必须转「已借出」"
        locked = db.query(BookCopy).filter(BookCopy.id == res.copy_id).first()
        assert locked.status == BookCopy.STATUS_AVAILABLE, (
            "预约锁着的另一册必须放回在馆（别留鬼锁）"
        )
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["available_quota"] == 29, (
        f"预约释放后额度应回 1（30−1 在借），实 {card['available_quota']}"
    )
