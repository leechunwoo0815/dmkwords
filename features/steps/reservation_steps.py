# features/steps/reservation_steps.py — FEAT-036 小程序预约 步骤定义
"""2026-10-13（第五十二轮）BDD 解封第二批 · **预约域** 7 个场景（1 个保留 `@draft`）。

预约是"线上占位"：锁一个副本 72 小时、占一个借阅额度，到店核销转借阅、超时释放、家长可取消。
四件事都走真链路（真实 MySQL + TestClient），释放走**真实定时任务**（管理端"手动触发"端点）。

**判据必须与实现同源（红线 108）——动手前逐条亲验**：
- 额度公式：`可借 = borrow_limit(30) − 在借(含逾期) − 预约中`（`circulation/service.py:345`）。
- 预约创建时的闸门顺序：`operation_locked → 有效会员 → 书目上架 → 押金(未缴/扣光/欠款硬拦)
  → 同书单一预约 → 副本在馆 → 额度`。
- **逾期不拦预约**（2026-09-21 甲方口径，PRD §5.4）：原场景写"有逾期禁止预约"是被作废的旧口径，
  本批按现行口径**改写场景文本**（不是弱化断言——旧口径本身已裁定作废）。
- 超时释放没有读侧惰性触发（只有 `reservation_expire_check` 任务，周期 300s），
  故 BDD 用管理端**手动触发端点** `POST /api/admin/tasks/reservation_expire_check/run`，属真实链路。
- 「预约期间副本转维护 → 自动锁定同书其他副本 / 无副本转异常态」**未实现**
  （`STATUS_EXCEPTION` 有常量无写入点、无改锁逻辑），该场景保留 `@draft`（G5）。
"""

from datetime import datetime, timedelta

from behave import given, then, when

from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _pay(context, child_id: int, order_type: str) -> None:
    o = context.client.post(
        "/api/admin/orders",
        json={"child_id": child_id, "order_type": order_type},
        headers=context.h,
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text


def _seed_borrowed_books(context, child_id: int, total: int, overdue: int = 0) -> None:
    """把在借补到 total 本（DB 造数，理由见 borrowing_steps 同款注释）。"""
    from backend.domain.catalog.models import Book, BookCopy
    from backend.domain.circulation.models import BorrowRecord

    now = datetime.now()
    with _db() as db:
        have = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )
        for i in range(have, total):
            is_overdue = i < overdue
            book = Book(title=f"造数书 {i + 1}", word_count=100, status=Book.STATUS_ON)
            db.add(book)
            db.flush()
            copy = BookCopy(book_id=book.id, copy_code=f"RSV{i + 1}-{child_id}", status="borrowed")
            db.add(copy)
            db.flush()
            db.add(
                BorrowRecord(
                    child_id=child_id,
                    copy_id=copy.id,
                    book_id=book.id,
                    borrowed_at=now - timedelta(days=20 if is_overdue else 1),
                    due_at=now - timedelta(days=5) if is_overdue else now + timedelta(days=13),
                    status=(
                        BorrowRecord.STATUS_OVERDUE if is_overdue else BorrowRecord.STATUS_ACTIVE
                    ),
                    renew_used=0,
                    is_deleted=0,
                )
            )
        db.commit()


def _book_with_copy(context, title: str) -> tuple[int, int]:
    """建一本上架书并返回 (book_id, 第一个副本 id)——预约会把这个副本锁成 reserved。"""
    created = context.client.post(
        "/api/admin/books",
        json={"isbn": None, "title": title, "word_count": 100, "copy_count": 1},
        headers=context.h,
    )
    assert created.status_code == 200, created.text
    book_id = created.json()["id"]
    force_book_on(context.client, context.h, book_id)
    copies = context.client.get(f"/api/admin/books/{book_id}/copies", headers=context.h).json()
    return book_id, copies[0]["id"]


def _reserve(context, book_id: int):
    return context.client.post(
        "/api/miniapp/reservations",
        json={"child_id": context.child["id"], "book_id": book_id},
        headers=context.mini,
    )


def _reservation_row(reservation_id: int):
    from backend.domain.reading.models import Reservation

    with _db() as db:
        return db.query(Reservation).filter(Reservation.id == reservation_id).first()


def _admin_reservations(context, status: str | None = None) -> list[dict]:
    params = {"status": status} if status else {}
    r = context.client.get("/api/admin/reservations", params=params, headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _copy_status(context, book_id: int, copy_id: int) -> str:
    rows = context.client.get(f"/api/admin/books/{book_id}/copies", headers=context.h).json()
    return [c for c in rows if c["id"] == copy_id][0]["status"]


def _card(context) -> dict:
    r = context.client.get(
        f"/api/admin/circulation/children/{context.child['id']}/card", headers=context.h
    )
    assert r.status_code == 200, r.text
    return r.json()


def _active_reservation_count(context) -> int:
    return len(
        [r for r in _admin_reservations(context, "active") if r["child_id"] == context.child["id"]]
    )


def _run_task(context, name: str) -> dict:
    r = context.client.post(f"/api/admin/tasks/{name}/run", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 背景 ----------


@given('家长 "{parent_name}" 孩子 "{child_name}" 是有效会员 押金已缴 无逾期 在借 {count:d} 本')
def step_bg(context, parent_name, child_name, count):
    context.h = _h(context.client)
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": parent_name, "phone": "13981037902"},
        headers=context.h,
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": child_name},
        headers=context.h,
    ).json()
    login = context.client.post(
        "/api/miniapp/login", json={"phone": "13981037902", "code": "1234"}
    ).json()
    context.parent, context.child = parent, child
    context.mini = {"Authorization": f"Bearer {login['token']}"}
    context.child_name = child_name
    dep = context.client.post(
        f"/api/admin/deposits/children/{child['id']}/orders", headers=context.h
    ).json()
    rc = context.client.post(
        f"/api/admin/orders/{dep['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=context.h,
    )
    assert rc.status_code == 200, rc.text
    _pay(context, child["id"], "observation_fee")
    _pay(context, child["id"], "formal_fee")
    _seed_borrowed_books(context, child["id"], count)
    card = _card(context)
    assert card["active_borrows"] == count and card["overdue_count"] == 0, card


# ---------- 场景：正常预约 ----------


@when("张女士为小明预约《{title}》")
def step_reserve(context, title):
    context.book_id, context.copy_id = _book_with_copy(context, title)
    r = _reserve(context, context.book_id)
    assert r.status_code == 200, r.text
    context.reservation = r.json()


@then('预约创建 该书副本进入 "{status}" 锁定 {hours:d} 小时')
def step_reserved(context, status, hours):
    row = _reservation_row(context.reservation["id"])
    assert row.status == "active", row.status
    assert _copy_status(context, context.book_id, context.copy_id) == status, (
        "副本应被锁成 reserved"
    )
    delta = row.expires_at - row.create_time  # BaseModel 的时间列名是 create_time
    assert abs(delta.total_seconds() - hours * 3600) < 120, f"锁定应 {hours}h：{delta}"


@then("预约占用借阅额度 在借加预约合计 {total:d} 本")
def step_quota_occupied(context, total):
    card = _card(context)
    assert card["active_borrows"] + _active_reservation_count(context) == total, card
    assert card["available_quota"] == card["borrow_limit"] - total, card


# ---------- 场景：预约占用额度校验 ----------


@given("小明在借 {count:d} 本")
def step_borrowed(context, count):
    _seed_borrowed_books(context, context.child["id"], count)
    assert _card(context)["active_borrows"] == count


@when("张女士尝试预约 {count:d} 本")
def step_reserve_many(context, count):
    context.attempts = []
    for i in range(count):
        book_id, copy_id = _book_with_copy(context, f"额度样例 {i + 1}")
        r = _reserve(context, book_id)
        context.attempts.append((r, book_id, copy_id))


@then("仅允许成功 {ok:d} 本 第三本被拒")
def step_quota_limit(context, ok):
    codes = [r.status_code for r, _, _ in context.attempts]
    assert codes[:ok] == [200] * ok and codes[ok:] == [422] * (len(codes) - ok), codes
    rejected = context.attempts[ok][0]
    assert "借阅额度已满" in rejected.json()["detail"], rejected.text


# ---------- 场景：有逾期时仍可预约（2026-09-21 口径） ----------


@given("小明有 {count:d} 本逾期")
def step_overdue(context, count):
    total = _card(context)["active_borrows"]
    _seed_borrowed_books(context, context.child["id"], total + count, overdue=total + count)
    assert _card(context)["overdue_count"] >= count, _card(context)


@when("张女士尝试预约")
def step_try_reserve(context):
    context.book_id, context.copy_id = _book_with_copy(context, "逾期仍可约样例")
    context.reservation_response = _reserve(context, context.book_id)


@then("预约成功创建")
def step_reserve_ok(context):
    assert context.reservation_response.status_code == 200, (
        f"2026-09-21 口径：逾期不拦预约（只占额度）：{context.reservation_response.text[:160]}"
    )
    context.reservation = context.reservation_response.json()


@then("逾期只占 {count:d} 个额度（可借数 = 30 − 在借含逾期 − 预约）")
def step_overdue_only_quota(context, count):
    card = _card(context)
    assert card["available_quota"] == card["borrow_limit"] - card["active_borrows"] - 1, card
    assert card["overdue_count"] >= count, card


# ---------- 场景：预约到期自动释放 ----------


@given("预约创建后 {hours:d} 小时内未到店核销")
def step_reserve_not_checked_out(context, hours):
    context.book_id, context.copy_id = _book_with_copy(context, "超时释放样例")
    r = _reserve(context, context.book_id)
    assert r.status_code == 200, r.text
    context.reservation = r.json()
    from backend.domain.reading.models import Reservation

    with _db() as db:
        row = db.query(Reservation).filter(Reservation.id == context.reservation["id"]).first()
        row.expires_at = datetime.now() - timedelta(hours=1)  # 真等 72h 不可行 → 直改到期时刻
        db.commit()


@then("预约自动失效 副本释放可借")
def step_auto_release(context):
    context.task_result = _run_task(context, "reservation_expire_check")
    row = _reservation_row(context.reservation["id"])
    assert row.status == "expired", f"超时应释放：{row.status}（任务结果 {context.task_result}）"
    assert _copy_status(context, context.book_id, context.copy_id) == "available", "副本应释放"


@then("家长收到预约已释放通知")
def step_release_notice(context):
    r = context.client.get(
        "/api/miniapp/notifications",
        params={"scene": "reservation.released"},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "家长消息中心应出现「预约已释放」通知"


# ---------- 场景：到店核销转借阅 ----------


@given("预约在有效期内")
def step_reservation_active(context):
    context.book_id, context.copy_id = _book_with_copy(context, "核销样例")
    r = _reserve(context, context.book_id)
    assert r.status_code == 200, r.text
    context.reservation = r.json()
    assert _reservation_row(context.reservation["id"]).status == "active"


@when("馆员扫码核销")
def step_checkout(context):
    r = context.client.post(
        f"/api/admin/reservations/{context.reservation['id']}/checkout", headers=context.h
    )
    assert r.status_code == 200, r.text
    context.checkout = r.json()


@then("预约转为借阅记录 预约名额释放")
def step_checked_out(context):
    assert context.checkout["borrow_record_id"], context.checkout
    assert _reservation_row(context.reservation["id"]).status == "checked_out"
    assert _copy_status(context, context.book_id, context.copy_id) == "borrowed", "副本应转为借出"
    assert _active_reservation_count(context) == 0, "核销后不再占预约名额"


# ---------- 场景：家长取消预约 ----------


@when("张女士取消预约")
def step_cancel(context):
    context.book_id, context.copy_id = _book_with_copy(context, "取消样例")
    r = _reserve(context, context.book_id)
    assert r.status_code == 200, r.text
    context.reservation = r.json()
    cancel = context.client.post(
        f"/api/miniapp/reservations/{context.reservation['id']}/cancel",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert cancel.status_code == 200, cancel.text
    context.cancel_response = cancel.json()


@then("副本立即释放 额度恢复")
def step_cancelled(context):
    assert _reservation_row(context.reservation["id"]).status == "cancelled"
    assert _copy_status(context, context.book_id, context.copy_id) == "available", "副本应立即释放"
    card = _card(context)
    assert card["available_quota"] == card["borrow_limit"] - card["active_borrows"], (
        f"额度应恢复（预约名额不再占用）：{card}"
    )


# ---------- 场景：同孩子同书单一预约 ----------


@given("小明对《{title}》已有进行中预约")
def step_existing_reservation(context, title):
    context.book_id, context.copy_id = _book_with_copy(context, title)
    r = _reserve(context, context.book_id)
    assert r.status_code == 200, r.text
    context.reservation = r.json()


@when("张女士再次预约同书")
def step_reserve_again(context):
    context.duplicate = _reserve(context, context.book_id)


@then("系统拒绝重复预约")
def step_duplicate_rejected(context):
    assert context.duplicate.status_code == 409, context.duplicate.text
    assert "进行中的预约" in context.duplicate.json()["detail"], context.duplicate.text
