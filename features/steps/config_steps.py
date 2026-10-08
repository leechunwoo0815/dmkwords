# features/steps/config_steps.py — FEAT-004 系统配置中心 步骤定义
"""2026-10-08（第三十七轮）BDD 解封：配置域最后一个 `@draft` 场景「已按旧规则进行的业务不受影响」。

这条是配置化的红线断言：**改配置只影响之后的业务，不能回头改写已发生的业务**。
走真实链路（真实 MySQL + TestClient）：建档 → 缴观察期费 → 缴押金 → 建 26 副本的书 → 借 25 本
→ 把 borrow_limit 从 30 改到 20 → 断言「在借仍是 25、卡面额度归零、再借被拒」。
"""

from behave import given, then

from tests.unit.test_wm10_concurrency import (
    _book_with_copies,
    _db,
    _family,
    _h,
    _pay,
    _pay_deposit,
)

#: 借满后仍留一本可借副本，用来证明"不是没书可借，而是额度拦下的"
BORROWED = 25


def _available_copy_ids(book_id: int) -> list[int]:
    from backend.domain.catalog.models import BookCopy

    with _db() as db:
        return [
            c.id
            for c in db.query(BookCopy).filter(BookCopy.book_id == book_id).order_by(BookCopy.id)
        ]


def _active_borrow_count(child_id: int) -> int:
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        return (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )


@given("小明已借 25 本书且尚未归还")
def step_given_25_books_borrowed(context):
    context.h = _h(context.client)
    _p, child, _mini = _family(context.client, context.h, "13981037031", name="小明")
    _pay(context.client, context.h, child["id"], "observation_fee")
    _pay_deposit(context.client, context.h, child["id"])
    # 25 本**不同的书**（同一书目在借期间不可重复借阅——红线：学生×书目唯一在借）
    copy_ids = []
    for i in range(BORROWED + 1):
        book_id = _book_with_copies(context.client, context.h, f"配置域演练书{i:02d}", copies=1)
        copy_ids.append(_available_copy_ids(book_id)[0])
    for copy_id in copy_ids[:BORROWED]:
        r = context.client.post(
            "/api/admin/circulation/borrow",
            json={"child_id": child["id"], "copy_id": copy_id},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
    assert _active_borrow_count(child["id"]) == BORROWED
    context.child_id = child["id"]
    context.spare_copy_id = copy_ids[BORROWED]


@then("借阅上限调整为 20 后 小明的在借书不受影响仅不能再新借")
def step_then_limit_change_keeps_existing(context):
    r = context.client.put(
        "/api/admin/configs/borrow_limit",
        json={"value": "20", "reason": "配置域 BDD：上限 30 → 20（旧业务不受影响演练）"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text

    # ① 旧业务不受影响：已借的 25 本一本不少
    assert _active_borrow_count(context.child_id) == BORROWED
    # ② 卡面按新规则算额度：20 − 25 → 归零（不出现负数）
    card = context.client.get(
        f"/api/admin/circulation/children/{context.child_id}/card", headers=context.h
    )
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["borrow_limit"] == 20, body
    assert body["available_quota"] == 0, body
    # ③ 有副本可借，但被新上限拦住（不是"没书"）
    denied = context.client.post(
        "/api/admin/circulation/borrow",
        json={"child_id": context.child_id, "copy_id": context.spare_copy_id},
        headers=context.h,
    )
    assert denied.status_code == 422, denied.text
    assert "上限" in denied.text, denied.text
