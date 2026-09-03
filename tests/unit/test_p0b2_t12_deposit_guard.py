# tests/unit/test_p0b2_t12_deposit_guard.py — P0 第二批 T12（B-12）押金扣光/欠赔偿拦截借书+预约
"""红测试：押金检查只拦 unpaid——fully_deducted（余额 0）或 unpaid_balance>0
（扣除超出押金未结清）的孩子借书/预约畅通无阻，与转让"无未结清赔偿款"口径双标。

修复：borrow/预约 create 守卫扩 fully_deducted + unpaid_balance>0
（borrow 保留 override 人工放行；预约为线上自助硬拦截）。
"""

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def _set_deposit(child_id: int, **kw):
    from backend.domain.billing.models import Deposit

    with _db() as db:
        dep = (
            db.query(Deposit).filter(Deposit.child_id == child_id, Deposit.is_deleted == 0).first()
        )
        assert dep is not None
        for k, v in kw.items():
            setattr(dep, k, v)
        db.commit()


def _borrow(client, h, child_id, book_id, override=None):
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        copy = db.query(BookCopy).filter(BookCopy.book_id == book_id).first()
    body = {"child_id": child_id, "copy_id": copy.id}
    if override:
        body["override_reason"] = override
    return client.post("/api/admin/circulation/borrow", json=body, headers=h)


def test_fully_deducted_blocks_borrow_override_allows(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981012001", "扣光借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    _set_deposit(c["id"], status="fully_deducted", available_amount=0)
    book = _book_with_copies(client, h, "扣光借书", 1)

    r = _borrow(client, h, c["id"], book)
    assert r.status_code == 422, f"押金已扣光应 422，实 {r.status_code} {r.text[:80]}"
    r2 = _borrow(client, h, c["id"], book, override="馆员核实后放行")
    assert r2.status_code == 200, f"override 应放行：{r2.status_code} {r2.text[:80]}"


def test_unpaid_balance_blocks_borrow(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981012002", "欠赔借孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    _set_deposit(c["id"], status="partially_deducted", unpaid_balance=20)
    book = _book_with_copies(client, h, "欠赔借书", 1)

    r = _borrow(client, h, c["id"], book)
    assert r.status_code == 422, f"未结清赔偿款应 422，实 {r.status_code} {r.text[:80]}"
    r2 = _borrow(client, h, c["id"], book, override="赔偿待结清放行")
    assert r2.status_code == 200, f"override 应放行：{r2.status_code} {r2.text[:80]}"


def test_fully_deducted_blocks_reservation(client: TestClient):
    h = _h(client)
    p, c, mini = _family(client, h, "13981012003", "扣约孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    _set_deposit(c["id"], status="fully_deducted", available_amount=0)
    book = _book_with_copies(client, h, "扣光约书", 1)

    r = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": book}, headers=mini
    )
    assert r.status_code == 422, f"押金已扣光预约应 422，实 {r.status_code} {r.text[:80]}"
