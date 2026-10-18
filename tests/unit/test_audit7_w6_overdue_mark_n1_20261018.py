# tests/unit/test_audit7_w6_overdue_mark_n1_20261018.py — W6 压测整改：逾期通知的 3N 查询
"""压测实测（2026-10-18，一次性压测库 6000 条逾期）：`overdue_mark` **104.39 秒**——
根因是"每条逾期各查一次 Child/Book/Parent"（3N 条 SELECT）+ 逐条 UPDATE + 逐条通知。

整改（`circulation/overdue_notify.py`）：三张表各一次 `IN` 批量取 → **查询数与记录数解耦**。
本用例把这条钉死：造 12 条逾期（12 个孩子挂在**同一个家长**下），断言
① 12 条全标记 + 12 条家长通知（行为不变）；② 对 `children`/`books`/`parents` 三张表的
SELECT **总数 ≤ 3**（与 N 无关）。

RED：把批量取数改回逐条查询（变异）→ 三张表 SELECT 数变成 36 → 断言红。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.database import engine
from tests.unit.test_p1_audit_20261015 import _db
from tests.unit.test_wm10_concurrency import _family, _h, _pay, _pay_deposit

KIDS = 12  # 同一家长下 12 个孩子，各借 1 本 → 12 条逾期
BATCHED_SELECT_BUDGET = 3  # children + books + parents 各一次


class _SqlCounter:
    """统计命中指定表名的 SELECT 次数（挂在 engine 上，任务自己开的 session 也覆盖）。"""

    def __init__(self, tables: tuple[str, ...]):
        self.tables = tables
        self.hits: list[str] = []

    def __enter__(self):
        def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            low = statement.lower()
            if not low.lstrip().startswith("select"):
                return
            for t in self.tables:
                if (
                    f"from {t} " in low
                    or f"from {t}\n" in low
                    or low.rstrip().endswith(f"from {t}")
                ):
                    self.hits.append(t)

        self._listener = _before
        event.listen(engine, "before_cursor_execute", _before)
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        event.remove(engine, "before_cursor_execute", self._listener)
        return False


def _overdue_borrows(client: TestClient, h: dict) -> tuple[int, int]:
    """1 家长 × 12 孩子 × 1 书目（12 副本）各借 1 本，全部改成"昨天到期"。返回 (parent_id, book_id)。"""
    p, first_child, _mini = _family(client, h, "13987000001", "逾期压测孩0")
    child_ids = [first_child["id"]]
    for i in range(1, KIDS):
        c = client.post(
            f"/api/admin/members/parents/{p['id']}/children",
            json={"name": f"逾期压测孩{i}"},
            headers=h,
        )
        child_ids.append(c.json()["id"])
    for cid in child_ids:
        _pay(client, h, cid, "observation_fee")
        _pay_deposit(client, h, cid)

    book = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "逾期压测书", "word_count": 1000, "copy_count": KIDS},
        headers=h,
    ).json()
    from tests.unit.helpers import force_book_on

    force_book_on(client, h, book["id"])
    copies = client.get(f"/api/admin/books/{book['id']}/copies", headers=h).json()

    for cid, copy in zip(child_ids, copies, strict=False):
        r = client.post(
            "/api/admin/circulation/borrow",
            json={"child_id": cid, "copy_id": copy["id"]},
            headers=h,
        )
        assert r.status_code == 200, r.text

    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        rows = (
            db.query(BorrowRecord)
            .filter(BorrowRecord.book_id == book["id"], BorrowRecord.is_deleted == 0)
            .all()
        )
        assert len(rows) == KIDS, len(rows)
        for rec in rows:
            rec.due_at = datetime.now() - timedelta(days=1)  # 定时任务造历史态：有意直改
        db.commit()
    return p["id"], book["id"]


def test_overdue_mark_query_count_decoupled_from_records(client: TestClient):
    """①+② 12 条逾期：全部标记 + 全部通知，且三张表的 SELECT 数 ≤ 3（与记录数解耦）。"""
    h = _h(client)
    parent_id, book_id = _overdue_borrows(client, h)

    with _SqlCounter(("children", "books", "parents")) as counter:
        r = client.post("/api/admin/tasks/overdue_mark/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["processed"] == KIDS, r.json()

    from backend.common.notification_models import Notification
    from backend.domain.circulation.models import BorrowRecord

    with _db() as db:
        overdue = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.book_id == book_id,
                BorrowRecord.status == BorrowRecord.STATUS_OVERDUE,
                BorrowRecord.is_deleted == 0,
            )
            .count()
        )
        notifs = (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == "borrow.overdue",
                Notification.is_deleted == 0,
            )
            .count()
        )

    assert overdue == KIDS, f"{KIDS} 条应全标逾期，实 {overdue}"
    assert notifs == KIDS, f"{KIDS} 条家长通知，实 {notifs}"
    assert len(counter.hits) <= BATCHED_SELECT_BUDGET, (
        f"三张表的 SELECT 应 ≤ {BATCHED_SELECT_BUDGET}（批量取数），实 {len(counter.hits)}：{counter.hits}"
    )


def test_overdue_mark_is_idempotent(client: TestClient):
    """③ 再跑一次：没有新的逾期（已 OVERDUE 不在扫描集）→ 标记 0、通知不重复。"""
    h = _h(client)
    parent_id, book_id = _overdue_borrows(client, h)
    assert client.post("/api/admin/tasks/overdue_mark/run", headers=h).json()["processed"] == KIDS
    again = client.post("/api/admin/tasks/overdue_mark/run", headers=h).json()
    assert again["processed"] == 0, again

    from backend.common.notification_models import Notification

    with _db() as db:
        notifs = (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == "borrow.overdue",
                Notification.is_deleted == 0,
            )
            .count()
        )
    assert notifs == KIDS, f"重复跑不应重复通知，实 {notifs}"


def test_overdue_mark_has_no_in_session_evaluation(client: TestClient, monkeypatch):
    """⑤ **O(N²) 回归守护**（W6 压测的**真根因**）：ORM 版 UPDATE 默认
    `synchronize_session="auto"` → 走 `BulkUDCompileState._get_matched_objects_on_criteria`，
    在 **session 身份映射**里逐对象求值 WHERE；本任务的 session 恰装着扫描出的全部逾期对象，
    于是每条 UPDATE 都要遍历一遍 → 6000 条 = **3600 万次**求值（cProfile 实测占 334s 里的 305s）。
    修法 `synchronize_session=False` → 这条路径**一次都不该走到**。

    本用例计数该函数被调用次数：修好后 **0 次**；退回默认 → 每条 UPDATE 一次（12 条 → 12 次）→ 红。
    """
    from sqlalchemy.orm import bulk_persistence as _bp

    h = _h(client)
    _overdue_borrows(client, h)

    calls = {"n": 0}
    real = _bp.BulkUDCompileState._get_matched_objects_on_criteria

    def _counting(cls, update_options, states):  # noqa: ANN001
        calls["n"] += 1
        return real.__func__(cls, update_options, states)

    monkeypatch.setattr(
        _bp.BulkUDCompileState, "_get_matched_objects_on_criteria", classmethod(_counting)
    )
    r = client.post("/api/admin/tasks/overdue_mark/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["processed"] == KIDS, r.json()
    assert calls["n"] == 0, (
        f"任务里出现了 session 内逐对象求值（O(N²) 征兆）：{calls['n']} 次"
        "——检查 ORM 版 UPDATE 是否缺 synchronize_session=False"
    )


def test_mark_and_notify_skips_record_returned_in_between(client: TestClient):
    """④ **扫描后、UPDATE 前被还书** → 状态守卫必须拦住（P1-F5 的真实窗口）。

    为什么单独造这个窗口：库是 **READ COMMITTED**（`database.py:18`，E-00 有意为之），
    所以"B 会话持旧快照"那种写法在 RC 下**复现不出竞态**——`test_wm10_concurrency` 的同名用例
    走的是"整段任务"，扫描阶段已读到 `returned` 就跳过了，**对条件 UPDATE 的守卫不敏感**
    （本批变异矩阵实证：去掉守卫它仍绿）。这里把窗口显式做出来：先拿到"扫描结果"对象，
    再让真实还书发生，最后把那份**过期快照**喂给 `mark_and_notify`。
    """
    from backend.domain.circulation.models import BorrowRecord
    from backend.domain.circulation.overdue_notify import mark_and_notify

    h = _h(client)
    parent_id, book_id = _overdue_borrows(client, h)

    db = _db()
    try:
        stale = (
            db.query(BorrowRecord)
            .filter(
                BorrowRecord.book_id == book_id,
                BorrowRecord.status == BorrowRecord.STATUS_ACTIVE,
                BorrowRecord.is_deleted == 0,
            )
            .first()
        )
        assert stale is not None
        copy_id = stale.copy_id
        rec_id = stale.id

        # 并发方：走真实 API 还书（提交后该行已是 returned）
        r = client.post(
            "/api/admin/circulation/return",
            json={"copy_id": copy_id, "condition": "normal"},
            headers=h,
        )
        assert r.status_code == 200, r.text

        marked = mark_and_notify(db, [stale])  # 喂**过期快照**：守卫必须拦下
        db.commit()
    finally:
        db.close()

    assert marked == 0, f"已还记录不该被标记逾期，实标 {marked} 条"
    with _db() as db:
        rec = db.query(BorrowRecord).filter(BorrowRecord.id == rec_id).first()
        assert rec.status == BorrowRecord.STATUS_RETURNED, f"已还记录被覆盖：{rec.status}"
        from backend.common.notification_models import Notification

        notifs = (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == "borrow.overdue",
                Notification.is_deleted == 0,
            )
            .count()
        )
    assert notifs == 0, f"不该给已还记录发逾期通知，实 {notifs} 条"
