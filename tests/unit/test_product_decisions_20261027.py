# tests/unit/test_product_decisions_20261027.py — 四条产品口径的机器判据
"""2026-10-27 高级产品经理拍板（docs/23）：

1. **活动默认仅会员**：新建活动未传 `member_only` → 默认仅会员；显式关闭后非会员可报名。
2. **活动结束时间与签到窗**：`end_at` 必须晚于 `start_at`；签到窗 = 开始前 60 分钟 ~
   结束后 30 分钟（早到/晚到两条拒绝判据）。
3. **AR 分档左闭右开**：`[lo, hi)`，AR 2.0/3.0 只落唯一一档，最后一档 `[4, ∞)`。
4. **押金退款展示**：订单状态保持 `paid`；列表按押金台账返回累计退款额与押金状态。

RED（可证伪）：
- 把 `ActivityCreateRequest.member_only` 默认改回 False → 第 1 条红；
- 去掉签到窗判断 / 把窗口边界改宽 → 第 2 条红；
- 把 `ar_max` 比较改回 `<=` → 第 3 条红；
- 把 `deposit_refunded_amount` 恒置 "0.00" → 第 4 条红。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child, _shift_activity


def _create_activity(client, h, *, title, start, end, fee=0, member_only=None, quota=5):
    payload = {
        "title": title,
        "activity_type": "book_club",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "location": "馆内一层",
        "max_quota": quota,
        "fee": fee,
        "description": "产品口径判据",
    }
    if member_only is not None:
        payload["member_only"] = member_only
    return client.post("/api/admin/activities", json=payload, headers=h)


def test_activity_defaults_to_member_only(client):
    """Q1：不传 member_only → 仅会员；显式 false → 非会员可报名。"""
    h = _h(client)
    start = datetime.now() + timedelta(days=3)
    r = _create_activity(
        client, h, title="默认仅会员会", start=start, end=start + timedelta(hours=2)
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/admin/activities/{r.json()['id']}", headers=h).json()
    assert detail["member_only"] is True, detail

    child, mini = _mk_child(client, h, "13900027001", "非会员孩", member=False)
    denied = client.post(
        f"/api/miniapp/activities/{r.json()['id']}/enroll",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert denied.status_code == 422, denied.text
    assert "仅限会员" in denied.json()["detail"], denied.text

    open_act = _create_activity(
        client,
        h,
        title="开放日会",
        start=start,
        end=start + timedelta(hours=2),
        member_only=False,
    )
    assert open_act.status_code == 200, open_act.text
    open_detail = client.get(f"/api/admin/activities/{open_act.json()['id']}", headers=h).json()
    assert open_detail["member_only"] is False, open_detail
    ok = client.post(
        f"/api/miniapp/activities/{open_act.json()['id']}/enroll",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert ok.status_code == 200, ok.text


def test_activity_end_at_validation_and_checkin_window(client, db):
    """Q2：end_at 必须晚于 start_at；签到窗早到/晚到都拒，窗口内成功。"""
    h = _h(client)
    start = datetime.now() + timedelta(days=3)
    bad = _create_activity(client, h, title="结束早于开始", start=start, end=start)
    assert bad.status_code == 422, bad.text
    assert "结束时间必须晚于开始时间" in bad.json()["detail"], bad.text

    act = _mk_activity(client, h, quota=2, fee=0)
    child, mini = _mk_child(client, h, "13900027002", "签到窗口孩", member=False)
    enrolled = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": child["id"]},
        headers=mini,
    )
    assert enrolled.status_code == 200, enrolled.text
    ticket = enrolled.json()["enrollment"]["ticket_code"]

    # 早到：开始前 2 小时（窗口提前 60 分钟开）
    _shift_activity(db, act["id"], start_delta_minutes=120, duration_minutes=120)
    early = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert early.status_code == 422, early.text
    assert "未开启" in early.json()["detail"], early.text

    # 晚到：开始前 3 小时 / 时长 1 小时 → 结束于 2 小时前，窗口已关（结束后 30 分钟）
    _shift_activity(db, act["id"], start_delta_minutes=-180, duration_minutes=60)
    late = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert late.status_code == 422, late.text
    assert "已关闭" in late.json()["detail"], late.text

    # 窗口内：开始前 10 分钟 ~ 结束后 2 小时
    _shift_activity(db, act["id"], start_delta_minutes=-10, duration_minutes=120)
    ok = client.post("/api/admin/activity-signin", json={"ticket_code": ticket}, headers=h)
    assert ok.status_code == 200, ok.text


def test_ar_range_is_left_closed_right_open(client, db):
    """Q3：[2,3) 只含 2.0/2.9；1.9 与 3.0 不落该档；[4,∞) 含 4.0。"""
    from backend.domain.catalog.models import Book

    h = _h(client)
    _, mini = _mk_child(client, h, "13900027003", "选书孩", member=False)
    for title, ar in (
        ("AR 1.9", "1.9"),
        ("AR 2.0", "2.0"),
        ("AR 2.9", "2.9"),
        ("AR 3.0", "3.0"),
        ("AR 4.0", "4.0"),
    ):
        db.add(Book(title=title, word_count=100, ar_level=ar, status=Book.STATUS_ON))
    db.commit()

    band = client.get("/api/miniapp/books?ar_min=2&ar_max=3&page_size=100", headers=mini)
    assert band.status_code == 200, band.text
    titles = {b["title"] for b in band.json()["items"]}
    assert {"AR 2.0", "AR 2.9"} <= titles, titles
    assert "AR 1.9" not in titles and "AR 3.0" not in titles, titles

    last = client.get("/api/miniapp/books?ar_min=4&page_size=100", headers=mini)
    assert last.status_code == 200, last.text
    assert "AR 4.0" in {b["title"] for b in last.json()["items"]}


def test_deposit_refund_display_keeps_order_paid(client, db):
    """Q4：押金退款不改订单状态；家长端/管理端都返回累计退款额 + 押金状态。"""
    from backend.domain.billing.models import Deposit, DepositLedger
    from backend.domain.identity.models import Order
    from backend.domain.identity.order_service import OrderService

    h = _h(client)
    child, mini = _mk_child(client, h, "13900027004", "押金退款孩", member=False)
    dep = Deposit(
        child_id=child["id"],
        amount=Decimal("1200"),
        available_amount=Decimal("900"),
        deducted_amount=Decimal("0"),
        supplemented_total=Decimal("0"),
        status=Deposit.STATUS_PARTIALLY_DEDUCTED,
        unpaid_balance=Decimal("0"),
    )
    db.add(dep)
    db.flush()
    order = Order(
        order_no=OrderService.new_order_no(),
        order_type=Order.TYPE_DEPOSIT,
        parent_id=child["parent_id"],
        child_id=child["id"],
        amount=Decimal("1200"),
        status=Order.STATUS_PAID,
        paid_at=datetime.now(),
        remark="押金缴纳",
    )
    db.add(order)
    db.flush()
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_REFUND,
            amount=Decimal("300"),
            balance_after=Decimal("900"),
            reason="部分退",
        )
    )
    db.commit()

    mine = client.get(f"/api/miniapp/orders?child_id={child['id']}", headers=mini)
    assert mine.status_code == 200, mine.text
    row = [o for o in mine.json() if o["id"] == order.id][0]
    assert row["status"] == Order.STATUS_PAID, row
    assert row["deposit_refunded_amount"] == "300.00", row
    assert row["deposit_status"] == Deposit.STATUS_PARTIALLY_DEDUCTED, row

    admin = client.get(f"/api/admin/orders?keyword={order.order_no}&page=1&page_size=20", headers=h)
    assert admin.status_code == 200, admin.text
    item = admin.json()["items"][0]
    assert item["status"] == Order.STATUS_PAID, item
    assert item["deposit_refunded_amount"] == "300.00", item
    assert item["deposit_status"] == Deposit.STATUS_PARTIALLY_DEDUCTED, item

    # 全额退：累计额 1200.00，状态 refunded；订单状态仍 paid
    dep.status = Deposit.STATUS_REFUNDED
    dep.available_amount = Decimal("0")
    db.add(
        DepositLedger(
            deposit_id=dep.id,
            entry_type=DepositLedger.ENTRY_REFUND,
            amount=Decimal("900"),
            balance_after=Decimal("0"),
            reason="退会全退",
        )
    )
    db.commit()
    mine2 = client.get(f"/api/miniapp/orders?child_id={child['id']}", headers=mini).json()
    row2 = [o for o in mine2 if o["id"] == order.id][0]
    assert row2["status"] == Order.STATUS_PAID, row2
    assert row2["deposit_refunded_amount"] == "1200.00", row2
    assert row2["deposit_status"] == Deposit.STATUS_REFUNDED, row2
