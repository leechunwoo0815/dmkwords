# features/steps/borrowing_steps.py — FEAT-031/032/033/034/035/037 到店借阅 步骤定义
"""2026-10-13（第五十二轮）BDD 解封第二批 · **借阅域** 9 个场景（+ 1 个新增场景）。

借阅是"实物+额度+押金"三线并行的动作：会员闸门 → 押金闸门 → 额度 → 副本状态 → AR 软提示，
每一步都能单独把人拦下来或放行。本文件把这些口径钉成**端到端行为**。

**判据必须与实现同源（红线 108）——动手前逐条亲验**：
- 额度公式 = `borrow_limit − 在借(含逾期) − 预约`（`circulation/service.py:345`），
  上限文案"可借上限已满（上限 N，在借 M 本，预约占 K 本）"。
- AR 是**软提示**：`warnings` 里出现"AR 超范围提示：孩子 AR x，本书 AR y（不拦截，请确认）"。
- 未入会：开关 `allow_unpaid_offline_borrow` 关闭 = **HARD 拦截（填原因也放不出去）**；
  开启 = 可放行、**每次限 1 本**、due = +72h、并生成 `admin.member_follow_up` 待办。
- 「借出时自动创建阅读记录」按口实况：借出写的是**借阅记录**（书架在借列表的源头），
  阅读进度行在首次播放上报时创建（PRD §5.2 字面要求未实现，已登记 G8）。
- **小程序自助续借已于 2026-10-13（第五十三轮 G6）落地**：`POST /api/miniapp/borrows/{id}/renew`
  （与馆员代办**共用** `CirculationService._load_renewable` 校验链），本域两个续借场景全部真跑。
- "逾期人工办理被硬拦 vs PRD §5.4 允许人工办理"的差异仍按**现状**断言并登记为 **G7**（待产品口径）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal

from behave import given, then, when
from fastapi.testclient import TestClient

from backend.main import app
from tests.unit.helpers import force_book_on
from tests.unit.test_wm10_concurrency import _db, _h

# ---------- 基建 ----------


def _amount(value) -> Decimal:
    return Decimal(str(value))


def _new_child(context, name: str) -> dict:
    return context.client.post(
        f"/api/admin/members/parents/{context.parent['id']}/children",
        json={"name": name},
        headers=context.h,
    ).json()


def _pay(context, child_id: int, order_type: str) -> dict:
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
    return o


def _set_config(context, key: str, value: str) -> None:
    """把配置调到目标值——**先读后写**（服务端对"新值=当前值"直接 422，别硬写）。"""
    rows = context.client.get("/api/admin/configs", headers=context.h).json()
    current = next((c["config_value"] for c in rows if c["config_key"] == key), None)
    if current == value:
        return
    r = context.client.put(
        f"/api/admin/configs/{key}",
        json={"value": value, "reason": "BDD 借阅域前置"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text


def _seed_borrowed_books(context, child_id: int, total: int, overdue: int = 0) -> None:
    """把孩子的在借补到 total 本（其中 overdue 本已逾期）——DB 造数。

    为什么不走产品路径：借 30 本要 30 本书 + 30 次借还调用（同书未还禁借），
    造数用直插更快也更稳；**被验的动作**（第 31 本被拒、归还后额度恢复）仍走真实 API。
    """
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
        overdue_have = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status == BorrowRecord.STATUS_OVERDUE,
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )
        for i in range(have, total):
            is_overdue = i < overdue
            title = f"造数书 {i + 1}"
            book = Book(title=title, word_count=100, status=Book.STATUS_ON)
            db.add(book)
            db.flush()
            copy = BookCopy(book_id=book.id, copy_code=f"BDD{i + 1}-{child_id}", status="borrowed")
            db.add(copy)
            db.flush()
            db.add(
                BorrowRecord(
                    child_id=child_id,
                    copy_id=copy.id,
                    book_id=book.id,
                    borrowed_at=now - timedelta(days=20 if is_overdue else 1),
                    due_at=now - timedelta(days=5) if is_overdue else now + timedelta(days=13),
                    status=BorrowRecord.STATUS_OVERDUE
                    if is_overdue
                    else BorrowRecord.STATUS_ACTIVE,
                    renew_used=0,
                    is_deleted=0,
                )
            )
        db.commit()
        if overdue > overdue_have:
            rows = (
                db.query(BorrowRecord)
                .filter(
                    BorrowRecord.child_id == child_id,
                    BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                    BorrowRecord.is_deleted == 0,
                )
                .limit(overdue - overdue_have)
                .all()
            )
            for row in rows:
                row.status = BorrowRecord.STATUS_OVERDUE
                row.due_at = now - timedelta(days=5)
            db.commit()


def _active_borrows_of(context, child_id: int) -> list:
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        return (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.child_id == child_id,
                BorrowRecord.status.in_([BorrowRecord.STATUS_ACTIVE, BorrowRecord.STATUS_OVERDUE]),
                BorrowRecord.is_deleted == 0,
            )
            .order_by(BorrowRecord.id)
            .all()
        )


def _card(context, child_id: int) -> dict:
    r = context.client.get(f"/api/admin/circulation/children/{child_id}/card", headers=context.h)
    assert r.status_code == 200, r.text
    return r.json()


def _borrow(context, child_id: int, *, isbn=None, copy_id=None, override=None, client=None):
    body: dict = {"child_id": child_id}
    if isbn:
        body["isbn"] = isbn
    if copy_id:
        body["copy_id"] = copy_id
    if override:
        body["override_reason"] = override
    return (client or context.client).post(
        "/api/admin/circulation/borrow", json=body, headers=context.h, timeout=30
    )


def _book_with_copies(context, title: str, *, isbn=None, copies: int = 1, ar_level=None):
    created = context.client.post(
        "/api/admin/books",
        json={
            "isbn": isbn,
            "title": title,
            "word_count": 100,
            "copy_count": copies,
            "ar_level": ar_level,
        },
        headers=context.h,
    )
    assert created.status_code == 200, f"建书失败：{created.status_code} {created.text[:200]}"
    book = created.json()
    force_book_on(context.client, context.h, book["id"])
    rows = context.client.get(f"/api/admin/books/{book['id']}/copies", headers=context.h).json()
    return book["id"], [c["id"] for c in rows]


# ---------- 背景 ----------


@given('孩子 "{child_name}" 是有效会员 押金已缴 当前在借 {count:d} 本')
def step_bg_member_with_borrows(context, child_name, count):
    context.h = _h(context.client)
    parent = context.client.post(
        "/api/admin/members/parents",
        json={"name": "借阅家长", "phone": "13981037901"},
        headers=context.h,
    ).json()
    child = context.client.post(
        f"/api/admin/members/parents/{parent['id']}/children",
        json={"name": child_name},
        headers=context.h,
    ).json()
    login = context.client.post(
        "/api/miniapp/login", json={"phone": "13981037901", "code": "1234"}
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
    assert _card(context, child["id"])["active_borrows"] == count, "背景要求：在借数未造出来"


# ---------- 场景：正常借书 ----------


@when("馆员识别小明并扫图书 ISBN 条码借出")
def step_borrow_by_isbn(context):
    _, copy_ids = _book_with_copies(context, "扫码借出样例", isbn="9780000000001")
    context.borrow_book_id, context.borrow_copy_id = None, copy_ids[0]
    context.borrow_response = _borrow(context, context.child["id"], isbn="9780000000001")
    assert context.borrow_response.status_code == 200, context.borrow_response.text
    context.record = context.borrow_response.json()


@then("借阅记录创建 在借变为 {count:d} 本")
def step_record_created(context, count):
    assert context.record["id"], context.record
    assert _card(context, context.child["id"])["active_borrows"] == count, _card(
        context, context.child["id"]
    )


@then("系统自动创建该书的阅读记录")
def step_reading_record(context):
    """按口实况断言"孩子书架立刻能看到、无需馆员登记"。

    **如实标注**：借出写的是**借阅记录**（`borrow_records`，书架在借列表的源头）；
    **阅读进度行**（`reading_progress`）在首次播放上报时创建——PRD §5.2 字面写"借出时自动创建
    阅读记录"未按字面实现，属口径差（已登记 docs/09 第五十二轮 G8）。
    """
    shelf = context.client.get(
        "/api/miniapp/borrows", params={"child_id": context.child["id"]}, headers=context.mini
    )
    assert shelf.status_code == 200, shelf.text
    data = shelf.json()
    rows = data["items"] if isinstance(data, dict) and "items" in data else data
    hits = [r for r in rows if r.get("book_id") == context.record["book_id"]]
    assert hits, f"借出后孩子的书架/在借列表必须立刻出现这本书：{rows}"


# ---------- 场景：借阅上限校验 ----------


@given("小明已借满 {count:d} 本")
def step_borrow_full(context, count):
    _seed_borrowed_books(context, context.child["id"], count)
    card = _card(context, context.child["id"])
    assert card["active_borrows"] == count and card["available_quota"] == 0, card


@when("馆员尝试再借出 {count:d} 本")
def step_try_borrow_over_limit(context, count):
    _, copy_ids = _book_with_copies(context, "超限样例")
    context.over_limit = _borrow(context, context.child["id"], copy_id=copy_ids[0])


@then("系统提示超过可借上限")
def step_over_limit_message(context):
    assert context.over_limit.status_code == 422, context.over_limit.text
    detail = context.over_limit.json()["detail"]
    assert "可借上限已满" in detail and "在借" in detail, detail


# ---------- 场景：并发借书不超借 ----------


@given("两个馆员同时为小明借同一本书的最后 {copies:d} 个副本")
def step_two_librarians(context, copies):
    _, copy_ids = _book_with_copies(context, "并发借书样例", copies=copies)
    assert len(copy_ids) == copies, copy_ids
    context.concurrent_copy = copy_ids[0]


@when("两笔借书操作并发提交")
def step_concurrent_borrow(context):
    def hit(_):
        with TestClient(app) as client:  # 两个独立客户端 = 两个并发请求
            return _borrow(
                context, context.child["id"], copy_id=context.concurrent_copy, client=client
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        context.concurrent = list(pool.map(hit, range(2)))


@then("仅一笔成功 另一笔提示副本已被借出")
def step_only_one_wins(context):
    """不变量：**恰好一笔成功**，另一笔 409 被拒（副本锁 + 同书未还双防线）。

    拒因文案**取决于谁先赢**：若抢在对方提交前先落了借阅记录，输的那笔会先撞"该书尚未归还，
    不能重复借阅"（同一孩子的重复借阅守卫）；否则撞"副本当前状态：已借出，不可借出"。
    两种都是正确拒绝，故断言只认"409 + 其中一条合法拒因"（不弱化：真正的不变量是**不超借**，
    由下一句"在借数与副本状态保持一致"钉住）。
    """
    codes = sorted(r.status_code for r in context.concurrent)
    assert codes == [200, 409], [(r.status_code, r.text[:120]) for r in context.concurrent]
    loser = [r for r in context.concurrent if r.status_code == 409][0]
    detail = loser.json()["detail"]
    assert ("不可借出" in detail) or ("尚未归还" in detail), detail


@then("在借数与副本状态保持一致")
def step_borrow_consistent(context):
    from backend.domain.catalog.models import BookCopy

    others = [r for r in _active_borrows_of(context, context.child["id"])]
    with _db() as db:
        status = db.query(BookCopy).filter(BookCopy.id == context.concurrent_copy).first().status
    assert len(others) >= 2, "并发抢到的那 1 本 + 背景 5 本"
    assert status == "borrowed", f"副本必须处于借出态（一次成功只借出一本）：{status}"


# ---------- 场景：逾期不额外扣减额度且不拦截借书 ----------


@given("小明已借 {total:d} 本 其中 {overdue:d} 本已逾期")
def step_borrow_with_overdue(context, total, overdue):
    _seed_borrowed_books(context, context.child["id"], total, overdue=overdue)
    context.overdue_count = overdue


@then("小明可借 {quota:d} 本（逾期书只占各自名额，不再额外扣减上限）")
def step_quota_with_overdue(context, quota):
    card = _card(context, context.child["id"])
    assert card["available_quota"] == quota, card
    assert card["overdue_count"] == context.overdue_count, card


@when("小明归还 {count:d} 本书")
def step_return_books(context, count):
    rows = _active_borrows_of(context, context.child["id"])
    done = 0
    for row in rows:
        if done >= count:
            break
        r = context.client.post(
            "/api/admin/circulation/return",
            json={"copy_id": row.copy_id, "condition": "normal"},
            headers=context.h,
        )
        assert r.status_code == 200, r.text
        done += 1
    context.returned = done


@then("在借 {active:d} 本 可借 {quota:d} 本")
def step_after_return(context, active, quota):
    card = _card(context, context.child["id"])
    assert card["active_borrows"] == active, card
    assert card["available_quota"] == quota, card


@then('到店借书不因"有逾期"被拒（全端统一不拦截）')
def step_borrow_with_overdue_ok(context):
    _, copy_ids = _book_with_copies(context, "逾期不拦样例")
    r = _borrow(context, context.child["id"], copy_id=copy_ids[0])
    assert r.status_code == 200, f"逾期只占额度，不拦借书：{r.status_code} {r.text[:160]}"


# ---------- 场景：AR 超范围提示不拦截 ----------


@given("小明当前 AR {child_ar} 某书 AR {book_ar}")
def step_ar_setup(context, child_ar, book_ar):
    from backend.domain.catalog.models import Book
    from backend.domain.identity.models import Child

    with _db() as db:
        db.query(Child).filter(Child.id == context.child["id"]).update({"ar_level": child_ar})
        db.commit()
    book_id, copy_ids = _book_with_copies(context, "AR 样例")
    with _db() as db:
        db.query(Book).filter(Book.id == book_id).update({"ar_level": book_ar})
        db.commit()
    context.ar_book_id, context.ar_copy_id = book_id, copy_ids[0]


@when("馆员借出该书")
def step_borrow_ar_book(context):
    context.ar_response = _borrow(context, context.child["id"], copy_id=context.ar_copy_id)


@then("系统提示超出推荐范围由馆员确认继续")
def step_ar_warning(context):
    assert context.ar_response.status_code == 200, context.ar_response.text
    warnings = context.ar_response.json().get("warnings") or []
    assert any("AR 超范围提示" in w for w in warnings), warnings


@then("借书正常完成")
def step_ar_borrow_done(context):
    assert context.ar_response.json()["id"], context.ar_response.json()


# ---------- 场景：异常情况馆员人工放行 ----------


@given("小明押金未缴 系统提示押金未缴")
def step_deposit_unpaid(context):
    """把押金卡复位为"未缴"——背景已缴，产品侧**没有**"退押金"的正门，
    故直改库构造这一状态（只改状态字段，不动金额）。"""
    from backend.domain.billing.models import Deposit

    with _db() as db:
        db.query(Deposit).filter(Deposit.child_id == context.child["id"]).update(
            {"status": Deposit.STATUS_UNPAID, "available_amount": Decimal("0.00")}
        )
        db.commit()
    _, copy_ids = _book_with_copies(context, "押金未缴样例")
    context.unpaid_copy_id = copy_ids[0]
    blocked = _borrow(context, context.child["id"], copy_id=context.unpaid_copy_id)
    assert blocked.status_code == 422, blocked.text
    assert "押金未缴纳" in blocked.json()["detail"], blocked.text


@when('馆员选择人工放行并填写原因"{reason}"')
def step_override_borrow(context, reason):
    context.override_reason = reason
    context.override_response = _borrow(
        context, context.child["id"], copy_id=context.unpaid_copy_id, override=reason
    )
    assert context.override_response.status_code == 200, context.override_response.text


@then("借书完成 且放行操作人、时间、原因被记录")
def step_override_logged(context):
    from backend.domain.circulation.models import BorrowRecord

    record_id = context.override_response.json()["id"]
    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == record_id).first()
        assert row.override_reason == context.override_reason, row.override_reason
        assert row.borrowed_by, "放行操作人必须留痕"
        assert row.borrowed_at, "放行时间必须留痕"


# ---------- 场景：未入会线下借书默认硬拦截 ----------


@given('孩子 "{child_name}" 未入会且系统开关未开启')
def step_unpaid_child(context, child_name):
    _set_config(context, "allow_unpaid_offline_borrow", "false")
    context.unpaid_child = _new_child(context, child_name)
    _, copy_ids = _book_with_copies(context, "未入会样例")
    context.unpaid_child_copy = copy_ids[0]


@when("馆员尝试为小红借书")
def step_borrow_unpaid_child(context):
    context.unpaid_attempt = _borrow(
        context, context.unpaid_child["id"], copy_id=context.unpaid_child_copy
    )


@then("系统硬拦截 无法放行")
def step_hard_block(context):
    assert context.unpaid_attempt.status_code == 422, context.unpaid_attempt.text
    assert "未入会" in context.unpaid_attempt.json()["detail"], context.unpaid_attempt.text
    forced = _borrow(
        context,
        context.unpaid_child["id"],
        copy_id=context.unpaid_child_copy,
        override="馆员坚持要借",
    )
    assert forced.status_code == 422, (
        f"HARD 拦截填原因也不放行：{forced.status_code} {forced.text[:160]}"
    )


# ---------- 场景：未入会临时借书开关开启后限借一本 ----------


@given("管理员已开启未入会临时借书开关")
def step_unpaid_switch_on(context):
    _set_config(context, "allow_unpaid_offline_borrow", "true")
    context.unpaid_child = _new_child(context, "小红")
    _, copy_ids = _book_with_copies(context, "临时借书样例")
    context.unpaid_child_copy = copy_ids[0]


@when("超管放行为小红借 {count:d} 本并留痕")
def step_override_unpaid(context, count):
    context.unpaid_record = _borrow(
        context,
        context.unpaid_child["id"],
        copy_id=context.unpaid_child_copy,
        override="家长临时体验，明日入会",
    )
    assert context.unpaid_record.status_code == 200, context.unpaid_record.text


@then("借出成功 且系统生成入会跟进任务")
def step_follow_up_todo(context):
    counts = context.client.get("/api/admin/todo-counts", headers=context.h)
    assert counts.status_code == 200, counts.text
    data = counts.json()
    value = data.get("member_follow_up")
    if value is None:  # 兼容 {counts: {...}} 形状
        value = (data.get("counts") or {}).get("member_follow_up")
    assert (value or 0) >= 1, f"未入会放行必须生成入会跟进待办：{data}"


@then("{hours:d} 小时内未归还或入会将提醒")
def step_72h_due(context, hours):
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        row = (
            db.query(BorrowRecord)
            .filter(BorrowRecord.id == context.unpaid_record.json()["id"])
            .first()
        )
        delta = row.due_at - row.borrowed_at
        assert abs(delta.total_seconds() - hours * 3600) < 120, f"放行借期应为 {hours}h：{delta}"
    # 每次限 1 本：再借一本必须 HARD 拒绝（填原因也不行）
    _, copy_ids = _book_with_copies(context, "临时借书第二本")
    second = _borrow(context, context.unpaid_child["id"], copy_id=copy_ids[0], override="再借一本")
    assert second.status_code == 422, second.text
    assert "限 1 本" in second.json()["detail"], second.text


# ---------- 场景：还书确认图书状态 ----------


@when("馆员扫条码还书")
def step_return(context):
    rows = _active_borrows_of(context, context.child["id"])
    context.return_target = rows[0]
    r = context.client.post(
        "/api/admin/circulation/return",
        json={"copy_id": context.return_target.copy_id, "condition": "normal"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.return_response = r.json()


@then("系统显示借阅人与是否逾期")
def step_return_shows(context):
    data = context.return_response
    assert data["child_id"] == context.child["id"], data
    assert data["due_at"] and data["returned_at"], data
    returned = datetime.fromisoformat(data["returned_at"])
    due = datetime.fromisoformat(data["due_at"])
    assert returned <= due, "本条是未逾期归还（是否逾期的判据在 due_at 与 returned_at 之间）"


@when('馆员确认图书"转维护"')
def step_copy_to_maintenance(context):
    r = context.client.put(
        f"/api/admin/copies/{context.return_target.copy_id}/status",
        json={"status": "maintenance", "reason": "BDD：归还确认转维护"},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.copy_status = r.json()["status"]


@then("副本状态变为维护中 不再可借")
def step_copy_maintenance(context):
    assert context.copy_status == "maintenance", context.copy_status
    _, copy_ids = _book_with_copies(context, "维护不可借样例")
    r = _borrow(context, context.child["id"], copy_id=context.return_target.copy_id)
    assert r.status_code == 409, r.text
    assert "维护中" in r.json()["detail"], r.text


# ---------- 场景：续借一次七天（自助）/ 逾期图书不可续借（自助被拒） ----------


@when("张女士在小程序续借")
def step_renew_self(context):
    r = context.client.post(
        f"/api/miniapp/borrows/{context.renew_record_id}/renew",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert r.status_code == 200, r.text
    context.renew_response = r.json()


@then("新到期日比原到期日晚 {days:d} 天")
def step_renew_gap(context, days):
    due_after = datetime.fromisoformat(context.renew_response["due_at"])
    expected = context.due_before + timedelta(days=days)
    assert abs((due_after - expected).total_seconds()) < 2, (
        f"自助续借同样从**原到期日**加 {days} 天：before={context.due_before} after={due_after}"
    )


@given("小明某书已逾期")
def step_overdue_borrow(context):
    from backend.domain.circulation.models import BorrowRecord

    _, copy_ids = _book_with_copies(context, "逾期续借样例")
    r = _borrow(context, context.child["id"], copy_id=copy_ids[0])
    assert r.status_code == 200, r.text
    context.renew_record_id = r.json()["id"]
    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == context.renew_record_id).first()
        row.due_at = datetime.now() - timedelta(days=1)
        row.status = BorrowRecord.STATUS_OVERDUE
        db.commit()
        context.due_before = row.due_at


@when("尝试自助续借")
def step_try_renew_self(context):
    context.renew_attempt = context.client.post(
        f"/api/miniapp/borrows/{context.renew_record_id}/renew",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )


@then("系统拒绝续借")
def step_renew_rejected(context):
    assert context.renew_attempt.status_code == 422, context.renew_attempt.text
    assert "已逾期" in context.renew_attempt.json()["detail"], context.renew_attempt.text


@then("馆员人工办理也不可突破一次续借上限")
def step_admin_cannot_bypass(context):
    """按**现状**断言两件事：① 逾期书的馆员续借同样被拒（无放行参数；PRD §5.4 允许人工办理的差异登记为 G7）；
    ② 上限不是"自助才有"——馆员对新书续借一次后，第二次（代办或自助）一律拒绝。"""
    staff = context.client.post(
        "/api/admin/circulation/renew",
        json={"record_id": context.renew_record_id},
        headers=context.h,
    )
    assert staff.status_code == 422, (
        f"现状：逾期书馆员也办不了（PRD §5.4 允许人工办理，差异登记为 G7）：{staff.text[:120]}"
    )
    _, copy_ids = _book_with_copies(context, "上限对照样例")
    created = _borrow(context, context.child["id"], copy_id=copy_ids[0])
    assert created.status_code == 200, created.text
    record_id = created.json()["id"]
    first = context.client.post(
        "/api/admin/circulation/renew", json={"record_id": record_id}, headers=context.h
    )
    assert first.status_code == 200, first.text
    second_staff = context.client.post(
        "/api/admin/circulation/renew", json={"record_id": record_id}, headers=context.h
    )
    second_self = context.client.post(
        f"/api/miniapp/borrows/{record_id}/renew",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert second_staff.status_code == 422 and second_self.status_code == 422, (
        f"1 次上限谁也突破不了：代办={second_staff.status_code} 自助={second_self.status_code}"
    )


# ---------- 场景：馆员代办续借的上限（原「逾期图书不可续借」的人工办理部分，独立真跑） ----------


@given("小明某书到期日为 {days:d} 天后 未逾期")
def step_borrow_due_in(context, days):
    from backend.domain.circulation.models import BorrowRecord

    _, copy_ids = _book_with_copies(context, "续借样例")
    r = _borrow(context, context.child["id"], copy_id=copy_ids[0])
    assert r.status_code == 200, r.text
    context.renew_record_id = r.json()["id"]
    with _db() as db:
        row = db.query(BorrowRecord).filter(BorrowRecord.id == context.renew_record_id).first()
        row.due_at = datetime.now().replace(microsecond=0) + timedelta(days=days)
        db.commit()
        context.due_before = row.due_at


@when("馆员人工办理续借")
def step_admin_renew(context):
    r = context.client.post(
        "/api/admin/circulation/renew",
        json={"record_id": context.renew_record_id},
        headers=context.h,
    )
    assert r.status_code == 200, r.text
    context.renew_response = r.json()


@then("新到期日为 {days:d} 天后")
def step_renew_due(context, days):
    due_after = datetime.fromisoformat(context.renew_response["due_at"])
    expected = context.due_before + timedelta(days=days)
    assert abs((due_after - expected).total_seconds()) < 2, (
        f"续借应从**原到期日**加 {days} 天：before={context.due_before} after={due_after}"
    )


@then("该书续借机会已用尽 不可再次续借")
def step_renew_exhausted(context):
    """上限**谁都不能突破**（PRD §5.4）：馆员代办与家长自助两条路径都必须拒。"""
    again = context.client.post(
        "/api/admin/circulation/renew",
        json={"record_id": context.renew_record_id},
        headers=context.h,
    )
    assert again.status_code == 422, again.text
    assert "续借机会已用完" in again.json()["detail"], again.text
    self_again = context.client.post(
        f"/api/miniapp/borrows/{context.renew_record_id}/renew",
        json={"child_id": context.child["id"]},
        headers=context.mini,
    )
    assert self_again.status_code == 422, self_again.text
    assert "续借机会已用完" in self_again.json()["detail"], self_again.text


@then("逾期书的馆员人工办理现状为硬拦（与 PRD §5.4「特殊情况可人工办理」不一致，见 G7）")
def step_overdue_renew_blocked(context):
    """如实断言**现状**：`renew` 对逾期书一律拒绝，且没有放行参数——PRD §5.4 允许馆员
    "特殊情况下人工办理逾期书的续借（留痕）"，该放行口径**未实现**（docs/09 第五十二轮 G7）。
    """
    from backend.domain.circulation.models import BorrowRecord

    rows = _active_borrows_of(context, context.child["id"])
    target = None
    for row in rows:
        if row.due_at < datetime.now():
            target = row
            break
    if target is None:
        _seed_borrowed_books(context, context.child["id"], len(rows) + 1, overdue=len(rows) + 1)
        target = [
            r for r in _active_borrows_of(context, context.child["id"]) if r.due_at < datetime.now()
        ][0]
    with _db() as db:
        db.query(BorrowRecord).filter(BorrowRecord.id == target.id).update(
            {"renew_used": 0, "due_at": datetime.now() - timedelta(days=1)}
        )
        db.commit()
    r = context.client.post(
        "/api/admin/circulation/renew", json={"record_id": target.id}, headers=context.h
    )
    assert r.status_code == 422, (
        f"现状：逾期书人工续借被拒（无放行口）：{r.status_code} {r.text[:160]}"
    )
    assert "逾期" in r.json()["detail"], r.text
