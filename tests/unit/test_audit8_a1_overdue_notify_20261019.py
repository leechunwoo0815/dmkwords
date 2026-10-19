# tests/unit/test_audit8_a1_overdue_notify_20261019.py — 第八轮审查 A 批：P1-1 逾期通知单写口
"""**缺陷（第八轮审查 P1-1，实测复现）**：`overdue_mark` 只扫 `status == ACTIVE`，而借阅台
`GET /api/admin/circulation/overdue` 会**惰性**把 ACTIVE 改成 OVERDUE（E-11/T20 展示守卫）——
于是"馆员先在借阅台看到的那批逾期书"永远不会被任务扫到，`borrow.overdue` 家长通知**永久漏发**。

**修复**：任务扫描面放宽为 `IN (ACTIVE, OVERDUE)`；已 OVERDUE 的行不再重复 CAS，但补发通知
（`_notified_keys` 预取去重，去重键 = `overdue:{到期日}`）。

**变异自证**（见 docs/09 第九十一批）：把 `overdue_mark` 的扫描条件改回 `== ACTIVE`
→ `test_list_first_still_notifies` **红**（其余用例仍绿，说明该用例正是这条缺陷的守护）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.database import get_session
from backend.domain.circulation.models import BorrowRecord
from tests.unit.test_wm11_tasks import _borrow, _full_member, _h, _notifs, _run


def _make_overdue(rec_id: int, days_ago: int = 1) -> datetime:
    """把借阅记录的到期日推到 N 天前（定时任务造历史态无 API 路径，有意直改）。"""
    with get_session() as db:
        rec = db.get(BorrowRecord, rec_id)
        rec.due_at = datetime.now() - timedelta(days=days_ago)
        db.commit()
        return rec.due_at


def _overdue_notifs(parent_id: int) -> list:
    return [n for n in _notifs(parent_id) if n.scene == "borrow.overdue"]


def test_overdue_mark_notifies_without_list(client: TestClient):
    """对照组：不碰借阅台列表，任务照常标记 + 通知。"""
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003101")
    rec = _borrow(client, h, c["id"], book["isbn"])
    due = _make_overdue(rec["id"])

    r = _run(client, h, "overdue_mark")
    assert r["status"] == "success"
    assert r["processed"] == 1

    with get_session() as db:
        assert db.get(BorrowRecord, rec["id"]).status == BorrowRecord.STATUS_OVERDUE
    notifs = _overdue_notifs(p["id"])
    assert len(notifs) == 1
    assert notifs[0].dedup_key == f"overdue:{due:%Y%m%d}"


def test_list_first_still_notifies(client: TestClient):
    """**本批核心守护（P1-1）**：借阅台列表先把状态改成 OVERDUE 之后，任务仍必须补发通知。

    RED（还原修复：任务只扫 ACTIVE）：列表调用后记录已是 OVERDUE → 任务 `processed=0`
    且 `borrow.overdue` 通知数为 0 —— 家长永远收不到逾期提醒。
    """
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003102")
    rec = _borrow(client, h, c["id"], book["isbn"])
    due = _make_overdue(rec["id"])

    resp = client.get("/api/admin/circulation/overdue", headers=h)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    with get_session() as db:  # 借阅台已惰性标记
        assert db.get(BorrowRecord, rec["id"]).status == BorrowRecord.STATUS_OVERDUE

    r = _run(client, h, "overdue_mark")
    assert r["status"] == "success"
    assert r["processed"] == 0, "已 OVERDUE 的行不该被重复 CAS（状态已是终态）"

    notifs = _overdue_notifs(p["id"])
    assert len(notifs) == 1, "借阅台先看到 ≠ 不通知：逾期通知必须补发"
    assert notifs[0].dedup_key == f"overdue:{due:%Y%m%d}"
    assert notifs[0].ref_id == str(rec["id"])


def test_overdue_mark_idempotent_after_widened_scan(client: TestClient):
    """扫描面放宽后仍幂等：第二轮不重复标记、不重复通知。"""
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003103")
    rec = _borrow(client, h, c["id"], book["isbn"])
    _make_overdue(rec["id"])

    first = _run(client, h, "overdue_mark")
    assert first["processed"] == 1
    second = _run(client, h, "overdue_mark")
    assert second["processed"] == 0, "存量逾期每轮重扫，但不得重复计数/重复通知"
    assert len(_overdue_notifs(p["id"])) == 1


def test_renew_then_overdue_notifies_again(client: TestClient):
    """续借（到期日变化）后再次逾期 → **两个周期各一条通知**（去重键带到期日，P1-2 语义不回退）。"""
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003104")
    rec = _borrow(client, h, c["id"], book["isbn"])
    first_due = _make_overdue(rec["id"], days_ago=3)
    assert _run(client, h, "overdue_mark")["processed"] == 1

    # 馆员人工续借（逾期书必填原因）→ 状态回 ACTIVE、到期日推后
    rr = client.post(
        "/api/admin/circulation/renew",
        json={"record_id": rec["id"], "reason": "家长来电续借"},
        headers=h,
    )
    assert rr.status_code == 200, rr.text
    with get_session() as db:
        row = db.get(BorrowRecord, rec["id"])
        assert row.status == BorrowRecord.STATUS_ACTIVE
        row.due_at = datetime.now() - timedelta(days=1)  # 第二个周期又逾期（到期日与首轮不同）
        db.commit()
        second_due = row.due_at

    assert second_due.date() != first_due.date()
    assert _run(client, h, "overdue_mark")["processed"] == 1
    keys = {n.dedup_key for n in _overdue_notifs(p["id"])}
    assert keys == {f"overdue:{first_due:%Y%m%d}", f"overdue:{second_due:%Y%m%d}"}, keys
