# tests/unit/test_g11_renewal_followup_20261014.py — PRD §3.7「续费跟进」落地回归（G11）
"""G11（2026-10-14 第五十八批落地）：PRD §3.7 明文要求——会员过期时馆员**人工放行**当次借书后，
系统要**自动生成一条「续费跟进」提醒**给运营专员（原话："确保这件事有人跟、不遗漏"）。

实现口径（改行为前先读）：
- 触发点：`circulation.service.borrow` 的**过期放行**分支（gate code=expired 且填了放行原因）；
  未填原因那次会被软闸拦下（422），因此不会生成跟进——真放行才需要人跟。
- 承载：管理端待办墙（scene=`admin.member_follow_up` + `ref_type=child`），标题「续费跟进」，
  与未入会的「入会跟进」共用 scene（靠标题区分）。
- 显示态：`todo_service._decide_member_followup` 按**有效会员状态**推导——未入会/已过期 → 待处理；
  成为有效会员 → **自动审结**（家长续费即归零，不靠人工销项）。
- 幂等：dedup_key = `renew:{到期日}`——同一到期周期重复放行只留一条，**下一个周期会重新提醒**
  （固定 key 会让次年再也提醒不出来，故带上到期日）。
- 关键回归点：放行常常发生在**状态任务落库之前**（库内还是 formal、只是到期日已过）→
  `_child_states` 必须按"有效会员状态"归一，否则待办会当场被判成已审结而挂不上墙。
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient


def _h(client, username="admin"):
    r = client.post("/api/admin/login", json={"username": username, "password": "dmkwords123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _family(client, h, phone, child_name="孩子"):
    p = client.post(
        "/api/admin/members/parents", json={"name": "家长", "phone": phone}, headers=h
    ).json()
    c = client.post(
        f"/api/admin/members/parents/{p['id']}/children", json={"name": child_name}, headers=h
    ).json()
    return p, c


def _pay_deposit(client, h, child_id):
    do = client.post(f"/api/admin/deposits/children/{child_id}/orders", headers=h).json()
    client.post(
        f"/api/admin/orders/{do['order_id']}/confirm-payment",
        json={"pay_method": "scan"},
        headers=h,
    )


def _buy(client, h, child_id, order_type, amount=None):
    o = client.post(
        "/api/admin/orders", json={"child_id": child_id, "order_type": order_type}, headers=h
    ).json()
    assert o.get("id"), o
    if amount is not None:  # 金额一律 Decimal 比（响应里可能是 "6000" 也可能是 "6000.00"）
        assert Decimal(o["amount"]) == Decimal(amount), o
    client.post(
        f"/api/admin/orders/{o['id']}/confirm-payment", json={"pay_method": "scan"}, headers=h
    )
    return o


def _expired_child(client, h, phone, name="过期孩", *, land_status=False):
    """formal 收款 → 到期日拨回昨天。

    `land_status=False`（默认）**刻意不落状态**：库内仍是 formal、只是到期日已过 —— 这正是
    "馆员当场放行、定时任务还没跑"的现场（G11 的关键回归点）。
    `land_status=True` 时跑真任务把状态落成 expired。
    """
    _p, c = _family(client, h, phone, name)
    _pay_deposit(client, h, c["id"])
    _buy(client, h, c["id"], "formal_fee")
    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_expire = date.today() - timedelta(days=1)
        db.commit()
    if land_status:
        r = client.post("/api/admin/tasks/member_expire_check/run", headers=h)
        assert r.status_code == 200, r.text
    return c


def _book(client, h, isbn, title="测试书"):
    from tests.unit.helpers import ensure_book_on

    b = client.post(
        "/api/admin/books", json={"isbn": isbn, "title": title, "word_count": 1000}, headers=h
    ).json()
    ensure_book_on(client, h, b["id"])
    return b


def _borrow(client, h, child_id, isbn, reason=None):
    body = {"child_id": child_id, "isbn": isbn}
    if reason:
        body["override_reason"] = reason
    return client.post("/api/admin/circulation/borrow", json=body, headers=h)


def _followups(client, h, child_id) -> list[dict]:
    inbox = client.get("/api/admin/admin-notifications", params={"page_size": 50}, headers=h).json()
    return [n for n in inbox["items"] if n["ref_type"] == "child" and n["ref_id"] == str(child_id)]


def _followup_count(client, h) -> int:
    return client.get("/api/admin/todo-counts", headers=h).json()["member_follow_up"]


# ---------- 用例 ----------


def test_expired_override_creates_renewal_followup(client: TestClient):
    """过期放行 → 待办墙出现「续费跟进」（待处理），且**状态未落库也要挂上墙**。"""
    h = _h(client)
    c = _expired_child(client, h, "13800001011")
    book = _book(client, h, "9789100020001")

    r = _borrow(client, h, c["id"], book["isbn"], reason="家长说明天来续费")
    assert r.status_code == 200, r.text

    rows = _followups(client, h, c["id"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["title"] == "续费跟进", row
    assert row["effective_status"] == "pending", row
    assert row["status_text"] == "待处理", row
    assert "家长说明天来续费" in row["content"], row
    assert _followup_count(client, h) == 1


def test_renewal_followup_auto_resolves_after_renewal(client: TestClient):
    """闭环：家长续费 → 同一行自动审结、待办数归零（不靠人工销项）。"""
    h = _h(client)
    c = _expired_child(client, h, "13800001012")
    book = _book(client, h, "9789100020002")
    assert _borrow(client, h, c["id"], book["isbn"], reason="先借回去，周末续费").status_code == 200
    row_id = _followups(client, h, c["id"])[0]["id"]
    assert _followup_count(client, h) == 1

    _buy(client, h, c["id"], "formal_fee", amount="6000.00")

    again = next(n for n in _followups(client, h, c["id"]) if n["id"] == row_id)
    assert again["effective_status"] == "done", again
    assert _followup_count(client, h) == 0


def test_unpaid_override_keeps_join_followup(client: TestClient):
    """不回归：**未入会**放行仍是「入会跟进」（同一 scene、不同标题）。"""
    h = _h(client)
    sw = client.put(
        "/api/admin/configs/allow_unpaid_offline_borrow",
        json={"value": "true", "reason": "G11 回归：未入会放行"},
        headers=h,
    )
    assert sw.status_code == 200, sw.text
    _p, c = _family(client, h, "13800001013", "未入会孩")
    _pay_deposit(client, h, c["id"])  # 隔离押金变量
    book = _book(client, h, "9789100020003")

    r = _borrow(client, h, c["id"], book["isbn"], reason="试读，周末办入会")
    assert r.status_code == 200, r.text

    rows = _followups(client, h, c["id"])
    assert len(rows) == 1, rows
    assert rows[0]["title"] == "入会跟进", rows[0]
    assert rows[0]["effective_status"] == "pending", rows[0]
    assert _followup_count(client, h) == 1


def test_renewal_followup_deduped_per_expiry_cycle(client: TestClient):
    """幂等按**到期周期**：同周期重复放行只留一条；下一个周期（新到期日）会重新提醒。"""
    h = _h(client)
    c = _expired_child(client, h, "13800001014")
    b1 = _book(client, h, "9789100020004")
    b2 = _book(client, h, "9789100020005")
    assert _borrow(client, h, c["id"], b1["isbn"], reason="明天续费").status_code == 200
    assert _borrow(client, h, c["id"], b2["isbn"], reason="明天续费").status_code == 200
    assert len(_followups(client, h, c["id"])) == 1, "同一到期周期只留一条"

    # 换一个到期周期（模拟"又是一年后过期"）：到期日变 → dedup_key 变 → 重新提醒
    from backend.database import get_session
    from backend.domain.identity.models import Child

    with get_session() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_expire = date.today() - timedelta(days=400)
        db.commit()
    b3 = _book(client, h, "9789100020006")
    assert _borrow(client, h, c["id"], b3["isbn"], reason="这次真的续").status_code == 200
    assert len(_followups(client, h, c["id"])) == 2, "下一个到期周期应重新提醒"


def test_expired_borrow_without_reason_blocked_and_no_followup(client: TestClient):
    """未填放行原因 → 软闸拦下（422），且**不生成**跟进（真放行才需要人跟）。"""
    h = _h(client)
    c = _expired_child(client, h, "13800001015", land_status=True)
    book = _book(client, h, "9789100020007")

    r = _borrow(client, h, c["id"], book["isbn"])
    assert r.status_code == 422, r.text
    assert "可放行" in r.json()["detail"], r.text
    assert _followups(client, h, c["id"]) == []
    assert _followup_count(client, h) == 0
