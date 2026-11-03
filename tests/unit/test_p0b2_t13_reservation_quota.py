# tests/unit/test_p0b2_t13_reservation_quota.py — 预约额度（2026-11-03 甲方改口径）
"""2026-11-03 甲方改口径：预约**不占可借额度**，改为独立 `reservation_limit`（默认 3）。

旧口径（T13 原始）：`在借 + 预约 <= borrow_limit`——新用户只要有 1 条预约，借阅操作台
就显示「可借 29」，感官上像额度算错（生产报障）。

新口径：
- 借阅上限只管在借：4 在借 + 2 预约（limit 6）仍可借到第 5、6 本；
- 预约独立上限 3，第 4 条预约 422；
- 核销预约转借阅时由 `borrow()` 校验借阅上限（6 在借时核销 422，还 1 本后可核销）；
- 卡片 `available_quota = 上限 − 在借`，且 `reservation_count/reservations` 可见。

测试设计（borrow_limit 调 6 缩小规模）。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _borrow(client, h, child_id, book_id):
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy = db.query(BookCopy).filter(BookCopy.book_id == book_id).first()
    return client.post(
        "/api/admin/circulation/borrow", json={"child_id": child_id, "copy_id": copy.id}, headers=h
    )


def _reserve(client, mini, child_id, book_id):
    return client.post(
        "/api/miniapp/reservations", json={"child_id": child_id, "book_id": book_id}, headers=mini
    )


def test_reservations_do_not_consume_borrow_quota(client: TestClient):
    h = _h(client)
    _, c, mini = _family(client, h, "13981013001", "预约不占额孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    r_cfg = client.put(
        "/api/admin/configs/borrow_limit", json={"value": "6", "reason": "T13 改口径"}, headers=h
    )
    assert r_cfg.status_code == 200, r_cfg.text

    hold_books = [_book_with_copies(client, h, f"不占额持书{i}", 1) for i in range(4)]
    resv_books = [_book_with_copies(client, h, f"不占额约书{i}", 1) for i in range(2)]
    extra_books = [_book_with_copies(client, h, f"不占额新书{i}", 1) for i in range(3)]
    for bid in hold_books:
        assert _borrow(client, h, c["id"], bid).status_code == 200
    for bid in resv_books:
        rr = _reserve(client, mini, c["id"], bid)
        assert rr.status_code == 200, rr.text

    # 卡面：4 在借 + 2 预约 → 可借 = 6-4 = 2（不是 0）；预约书目可见
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["available_quota"] == 2, card
    assert card["reservation_count"] == 2, card
    assert len(card["reservations"]) == 2, card
    assert all(r["book_title"] for r in card["reservations"]), card["reservations"]

    # 预约不再占额度：第 5、6 本照常借出；第 7 本才撞在借上限
    r5 = _borrow(client, h, c["id"], extra_books[0])
    assert r5.status_code == 200, f"4 在借+2 预约仍应能借第 5 本：{r5.status_code} {r5.text[:80]}"
    r6 = _borrow(client, h, c["id"], extra_books[1])
    assert r6.status_code == 200, f"5 在借+2 预约仍应能借第 6 本：{r6.status_code} {r6.text[:80]}"
    r7 = _borrow(client, h, c["id"], extra_books[2])
    assert r7.status_code == 422, f"6 在借=上限，第 7 本应 422：{r7.status_code} {r7.text[:80]}"

    # 核销仍校验借阅上限：6 在借时核销 422，预约保持 active（事务回滚）
    rid1 = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()[0]["id"]
    ck1 = client.post(f"/api/admin/reservations/{rid1}/checkout", headers=h)
    assert ck1.status_code == 422, f"6 在借时核销应 422：{ck1.status_code} {ck1.text[:80]}"
    still = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()
    assert any(x["id"] == rid1 for x in still), "核销失败后预约必须仍 active（不能白丢锁定）"

    # 还 1 本 → 5 在借，核销第 1 单成功（转为 6 在借）；第 2 单再核销 422
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        rec = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == c["id"],
                BorrowRecord.book_id == hold_books[0],
                BorrowRecord.is_deleted == 0,
            )
            .first()
        )
        assert rec is not None
        copy_id = rec.copy_id
    rb = client.post("/api/admin/circulation/return", json={"copy_id": copy_id}, headers=h)
    assert rb.status_code == 200, rb.text
    ck2 = client.post(f"/api/admin/reservations/{rid1}/checkout", headers=h)
    assert ck2.status_code == 200, ck2.text
    rid2 = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()[0]["id"]
    ck3 = client.post(f"/api/admin/reservations/{rid2}/checkout", headers=h)
    assert ck3.status_code == 422, f"核销后 6 在借，第二单应 422：{ck3.status_code}"


def test_reservation_limit_is_independent(client: TestClient):
    """预约独立上限：默认 3，第 4 条 422；与在借数无关。"""
    h = _h(client)
    _, c, mini = _family(client, h, "13981013002", "预约上限孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    books = [_book_with_copies(client, h, f"预约上限书{i}", 1) for i in range(4)]
    for bid in books[:3]:
        rr = _reserve(client, mini, c["id"], bid)
        assert rr.status_code == 200, rr.text
    r4 = _reserve(client, mini, c["id"], books[3])
    assert r4.status_code == 422, f"第 4 条预约应 422：{r4.status_code} {r4.text[:100]}"
    assert "预约已达上限" in r4.text


# ---- B7（插修5）：预约管理孩子/家长模糊搜索 + 状态"全部" ----


def test_b7_reservation_keyword_search(client):
    """keyword 模糊匹配孩子名/家长手机号；空/None 全量；status 空串不过滤。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13981034001", "搜索特异孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    book = _book_with_copies(client, h, "搜索预约书", 1)
    r = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book}, headers=mini
    )
    assert r.status_code == 200, r.text

    # keyword=孩子名 → 命中
    r1 = client.get("/api/admin/reservations?keyword=搜索特异", headers=h)
    assert r1.status_code == 200, r1.text
    assert any(row["child_name"] == "搜索特异孩" for row in r1.json())
    # keyword=家长手机号 → 命中
    r2 = client.get("/api/admin/reservations?keyword=13981034001", headers=h)
    assert any(row["child_name"] == "搜索特异孩" for row in r2.json())
    # keyword=不匹配 → 空
    r3 = client.get("/api/admin/reservations?keyword=不存在的名字xyz", headers=h)
    assert all(row["child_name"] != "搜索特异孩" for row in r3.json())
    # 无 keyword → 全量回归
    r4 = client.get("/api/admin/reservations", headers=h)
    assert any(row["child_name"] == "搜索特异孩" for row in r4.json())
