# tests/unit/test_borrow_quota_overdue.py — 2026-09-21 甲方口径修订回归锁
"""口径变更（用户 2026-09-21 拍板；文档先行已落地：PRD §5.4 现行口径 / CLAUDE.md §二 借阅并发红线 /
docs/任务包-20260921-借阅操作台扫码闭环）：逾期**不拦截**、额度**只扣一次**。

1. 卡面「可借」必须等于服务端真实可借数：`上限 − 在借总数(含逾期) − 预约中`。
   历史 bug（两个方向）：`child_card()` 写成 `max(0, limit - len(overdue) - len(active))`，而 `active` 已含逾期
   → 每本逾期**多扣 1**（无预约时卡面比 `borrow()` 实际放行的少 1——用户 2026-09-21 报障原话"你现在是扣 2 本了"）；
   同时该式**没减预约中** → 有预约时反而多报。两类误差在"1 本逾期 + 1 个预约"时正好抵消（演示孩现场即此巧合），
   故必须用同源公式 + 本测试锁死，不能靠肉眼对数字。
2. 小程序预约不再因"有逾期未还"被拒（原硬拦 `有逾期未还图书，请先归还` 已按"全端统一不拦截"移除）；
   **逾期中的那本书本身仍不可续借**（另一条规则，未变）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _first_copy_id(book_id: int) -> int:
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        return db.query(BookCopy).filter(BookCopy.book_id == book_id).first().id


def _borrow_then_make_overdue(client: TestClient, h: dict, child_id: int, book_id: int) -> int:
    """借出该副本 → 把到期日改到过去（同一条记录既是"在借"也是"逾期"），返回记录 id。"""
    from backend.domain.circulation.models import BorrowRecord

    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": child_id, "copy_id": _first_copy_id(book_id)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    with _db() as db:
        rec = db.query(BorrowRecord).filter(BorrowRecord.id == rid).first()
        rec.due_at = datetime.now() - timedelta(days=2)
        db.commit()
    return rid


def test_card_quota_counts_overdue_once(client: TestClient):
    """一本逾期书只占 1 个名额：卡面 29（旧实现给 28），且这 1 个名额必须真能借出来。"""
    h = _h(client)
    _p, c, _mini = _family(client, h, "13981013001", "逾期占额孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    b1 = _book_with_copies(client, h, "逾期占额书一", 1)
    b2 = _book_with_copies(client, h, "逾期占额书二", 1)
    _borrow_then_make_overdue(client, h, c["id"], b1)

    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 1
    assert card["overdue_count"] == 1
    assert card["available_quota"] == 29, (
        f"逾期只应扣 1 次（旧实现重复扣减给 28），实 {card['available_quota']}"
    )

    # 卡面数字必须可兑现——还能再借 1 本，借完变 28（否则就是"显示比实际少"的老毛病）
    r = client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": c["id"], "copy_id": _first_copy_id(b2)},
        headers=h,
    )
    assert r.status_code == 200, f"卡面显示可借 1，服务端应放行：{r.status_code} {r.text[:120]}"
    card = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert card["active_borrows"] == 2
    assert card["available_quota"] == 28


def test_card_quota_subtracts_reservation(client: TestClient):
    """预约占额度：有 1 个在预约时卡面少 1（旧实现不减预约 → 多报 1）。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13981013003", "预约占额孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    b_want = _book_with_copies(client, h, "预约占额书", 1)

    before = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert before["available_quota"] == 30
    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": b_want},
        headers=mini,
    )
    assert r.status_code == 200, r.text
    after = client.get(f"/api/admin/circulation/children/{c['id']}/card", headers=h).json()
    assert after["active_borrows"] == 0
    assert after["available_quota"] == 29, f"预约应占 1 个额度，实 {after['available_quota']}"


def test_overdue_does_not_block_reservation(client: TestClient):
    """有逾期不再拦预约（全端统一不拦截）；但逾期中的那本书仍不可续借。"""
    h = _h(client)
    _p, c, mini = _family(client, h, "13981013002", "逾期预约孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    b_overdue = _book_with_copies(client, h, "逾期预约书一", 1)
    b_want = _book_with_copies(client, h, "逾期预约书二", 1)
    rid = _borrow_then_make_overdue(client, h, c["id"], b_overdue)

    r = client.post(
        "/api/miniapp/reservations",
        json={"child_id": c["id"], "book_id": b_want},
        headers=mini,
    )
    assert r.status_code == 200, (
        f"2026-09-21 口径：有逾期不再拦预约，实 {r.status_code} {r.text[:120]}"
    )

    rr = client.post("/api/admin/circulation/renew", json={"record_id": rid}, headers=h)
    assert rr.status_code == 422, f"逾期中的书仍不可续借，实 {rr.status_code}"
    assert "逾期" in rr.json()["detail"]
