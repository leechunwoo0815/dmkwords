# tests/unit/test_p0b2_t13_reservation_quota.py — P0 第二批 T13（B-11）借书额度计入预约
"""红测试：宪法"预约占额度"只在预约 create 侧执行（在借+预约 ≤ 上限），
borrow 侧只数 active 在借 → 28 在借+2 预约的孩子还能借第 29、30 本（实际占用 32）。

修复：borrow 额度公式 quota = borrow_limit - active_count - reservation_count。

测试设计（borrow_limit 调 6 缩小规模）：
- 4 在借 + 2 active 预约 = 上限 6 → 借第 5 本 422（RED：当前 quota=6-4=2 → 200）
- 预约核销（checkout）后额度净变化 0：checkout 1 单（5 在借+1 预约=6）仍 422；
  checkout 第 2 单（6 在借+0 预约=6）仍 422；还 1 本（5 在借）→ 200 恢复
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


def test_reservation_counts_toward_borrow_quota(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981013001", "预约占额孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    r_cfg = client.put(
        "/api/admin/configs/borrow_limit", json={"value": "6", "reason": "T13 测试"}, headers=h
    )
    assert r_cfg.status_code == 200, r_cfg.text

    hold_books = [_book_with_copies(client, h, f"占额持书{i}", 1) for i in range(4)]
    resv_books = [_book_with_copies(client, h, f"占额约书{i}", 2) for i in range(2)]
    for bid in hold_books:
        assert _borrow(client, h, c["id"], bid).status_code == 200
    for bid in resv_books:
        rr = client.post(
            "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": bid}, headers=mini
        )
        assert rr.status_code == 200, rr.text

    extra = _book_with_copies(client, h, "占额新书", 1)
    r = _borrow(client, h, c["id"], extra)
    assert r.status_code == 422, (
        f"4 在借+2 预约=上限 6，应 422，实 {r.status_code} {r.text[:80]}（RED=额度不含预约）"
    )

    # 核销净变化 0：checkout 1 单 → 5 在借 + 1 预约 = 6 仍满
    rid1 = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()[0]["id"]
    ck1 = client.post(f"/api/admin/reservations/{rid1}/checkout", headers=h)
    assert ck1.status_code == 200, ck1.text
    r2 = _borrow(client, h, c["id"], extra)
    assert r2.status_code == 422, f"核销后 5 在借+1 预约=6 仍满，实 {r2.status_code}"

    # checkout 第 2 单 → 6 在借 + 0 预约 = 6 仍满
    rid2 = client.get(f"/api/miniapp/reservations?child_id={c['id']}", headers=mini).json()[0]["id"]
    ck2 = client.post(f"/api/admin/reservations/{rid2}/checkout", headers=h)
    assert ck2.status_code == 200, ck2.text
    r3 = _borrow(client, h, c["id"], extra)
    assert r3.status_code == 422, f"核销后 6 在借=6 仍满，实 {r3.status_code}"

    # 还 1 本在借书 → 5 在借 < 6 → 恢复可借（回归：正常路径不受影响）
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
    r4 = _borrow(client, h, c["id"], extra)
    assert r4.status_code == 200, f"还 1 本后 5 在借<6 应可借，实 {r4.status_code} {r4.text[:80]}"


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
