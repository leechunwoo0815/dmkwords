# tests/unit/test_audit8_a1_overdue_notify_20261019.py — 第八轮审查 A 批：P1-1 逾期通知单写口
"""**缺陷（第八轮审查 P1-1，实测复现）**：`overdue_mark` 只扫 `status == ACTIVE`，于是**已是
OVERDUE 的存量行**永远不进扫描面，`borrow.overdue` 家长通知**永久漏发**（当时的触发源是借阅台
`GET /api/admin/circulation/overdue` 的惰性标记——E-11/T20 展示守卫）。

**修复**：任务扫描面放宽为 `IN (ACTIVE, OVERDUE)`；已 OVERDUE 的行不再重复 CAS，但补发通知
（`_notified_keys` 预取去重，去重键 = `overdue:{到期日}`）。

**第九轮审查（P3）后的口径变化**：借阅台 GET 已**不再写库**（`overdue_list` 改只读 + 锁定读
探针），状态推进的唯一入口就是本任务——P1-1 那条放宽（扫 `IN (ACTIVE, OVERDUE)`）因此更必需，
守护用例改为**直接造"已是 OVERDUE 的存量行"**（不再借道 GET）。

**变异自证**（见 docs/09 第九十一批）：把 `overdue_mark` 的扫描条件改回 `== ACTIVE`
→ `test_stock_overdue_row_still_notified` **红**（其余用例仍绿，说明该用例正是这条缺陷的守护）。
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
    """**借阅台先看 ≠ 不通知**：GET 名单只读（不推进状态），随后任务照常标记 + 通知。

    第九轮 P3 改写：原先这里还断言"借阅台已惰性标记为 OVERDUE"——那是旧实现（GET 写库）。
    现在**反向断言**：GET 之后库里仍是 ACTIVE（GET 不写库），且任务仍把这一行标记并通知，
    即"馆员先看一眼"不会让这条逾期书漏通知（P1-1 的用户可见后果不因去写库而回退）。
    """
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003102")
    rec = _borrow(client, h, c["id"], book["isbn"])
    due = _make_overdue(rec["id"])

    resp = client.get("/api/admin/circulation/overdue", headers=h)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1
    with get_session() as db:  # 第九轮 P3：GET 只读，不再惰性写库
        assert db.get(BorrowRecord, rec["id"]).status == BorrowRecord.STATUS_ACTIVE, (
            "GET /circulation/overdue 不得写库：逾期状态只能由 overdue_mark 任务推进"
        )

    r = _run(client, h, "overdue_mark")
    assert r["status"] == "success"
    assert r["processed"] == 1

    notifs = _overdue_notifs(p["id"])
    assert len(notifs) == 1, "借阅台先看到 ≠ 不通知：逾期通知必须照发"
    assert notifs[0].dedup_key == f"overdue:{due:%Y%m%d}"
    assert notifs[0].ref_id == str(rec["id"])


def test_stock_overdue_row_still_notified(client: TestClient):
    """**P1-1 核心守护**：已是 OVERDUE 的**存量行**（历轮任务/历史数据）也必须补发通知。

    第九轮 P3 改写：旧版借道借阅台 GET 造出 OVERDUE（GET 已改成只读，这条路没了），
    这里改成直接落库造存量态——这正是生产上存量行来源（历史任务跑过、迁移数据）。

    RED（把 `overdue_mark` 扫描面还原成 `== ACTIVE`）：存量行被跳过 → `processed=0` 且
    `borrow.overdue` 通知数为 0，家长永远收不到这条逾期提醒。
    """
    h = _h(client)
    p, c, book = _full_member(client, h, phone="13800003105")
    rec = _borrow(client, h, c["id"], book["isbn"])
    due = _make_overdue(rec["id"])
    with get_session() as db:  # 存量态：状态已是 OVERDUE，但还没发过通知
        db.get(BorrowRecord, rec["id"]).status = BorrowRecord.STATUS_OVERDUE
        db.commit()

    r = _run(client, h, "overdue_mark")
    assert r["status"] == "success"
    assert r["processed"] == 0, "已 OVERDUE 的行不该被重复 CAS（状态已是终态）"

    notifs = _overdue_notifs(p["id"])
    assert len(notifs) == 1, "存量 OVERDUE 行必须补发通知（扫描面必须含 OVERDUE）"
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
