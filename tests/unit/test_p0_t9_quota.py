# tests/unit/test_p0_t9_quota.py — P0 第一批 T9（E-9）借书额度单次扣减
"""红测试：quota = borrow_limit - overdue_count - active_count（逾期在 active 内，
逾期一本双扣）。修复：quota = borrow_limit - active_count。

- 5 本全逾期：修复后额度 25；当前实现 20（active=25 时再借被拒 = RED）
- mixed（2 逾期 + 3 正常，共 5 active）：修复后额度 25
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _h, _family, _pay, _pay_deposit, _book_with_copies


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


def _overdue_all(client, h, child_id):
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        db.query(BorrowRecord).filter(
            BorrowRecord.child_id == child_id,
            BorrowRecord.status.in_(
                [BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]
            ),
        ).update({"due_at": datetime.now() - timedelta(days=3)})
        db.commit()


def test_borrow_quota_single_deduction_all_overdue(client: TestClient):
    """5 本全逾期 → 额度 25（当前 20 = RED）：active=25 时再借应允许。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13980009901", "额度孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    overdue_books = [_book_with_copies(client, h, f"逾期书{i}", 1) for i in range(5)]
    normal_books = [_book_with_copies(client, h, f"可借书{i}", 1) for i in range(21)]

    for bid in overdue_books:
        assert _borrow(client, h, c["id"], bid).status_code == 200
    _overdue_all(client, h, c["id"])
    for bid in normal_books[:20]:
        assert _borrow(client, h, c["id"], bid).status_code == 200

    r = _borrow(client, h, c["id"], normal_books[20])
    assert r.status_code == 200, f"额度 25 应允许第 21 本，实 {r.status_code} {r.text[:80]}"


def test_borrow_quota_single_deduction_mixed(client: TestClient):
    """mixed（2 逾期 + 3 正常，5 active）：额度 25。"""
    h = _h(client)
    p, c, mini = _family(client, h, "13980009902", "额度混孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    overdue_books = [_book_with_copies(client, h, f"混逾期书{i}", 1) for i in range(2)]
    normal_books = [_book_with_copies(client, h, f"混可借书{i}", 1) for i in range(24)]

    for bid in overdue_books:
        assert _borrow(client, h, c["id"], bid).status_code == 200
    for bid in normal_books[:3]:
        assert _borrow(client, h, c["id"], bid).status_code == 200
    _overdue_all(client, h, c["id"])  # 2 本已逾期 + 3 正常 = 5 active
    for bid in normal_books[3:23]:
        assert _borrow(client, h, c["id"], bid).status_code == 200

    r = _borrow(client, h, c["id"], normal_books[23])
    assert r.status_code == 200, f"额度 25 应允许，实 {r.status_code} {r.text[:80]}"