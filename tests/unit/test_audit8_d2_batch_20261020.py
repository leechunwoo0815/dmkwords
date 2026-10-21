# tests/unit/test_audit8_d2_batch_20261020.py — 第八轮大审查 D 批（下）P2-41
"""定时任务批量链路：**消 N+1**（查询数与记录数解耦）+ **分块提交**（不再整批单事务长锁）。

背景（实测口径，非静态推断）：
- 到期提醒类任务原先**每条记录**各查一次 `Child`/`Book`/`Parent`（3N 条 SELECT）
  再加一条通知存在性 SELECT（第 4N 条）——6000 条 = 2.4 万条 SQL；
- 这些任务原先**整批一个事务**（6000 条逾期 ≈ 8000 条 SQL 全在一个事务里，持锁数十秒），
  而它们逐条独立（状态守卫 UPDATE + 带去重键的通知），可安全分块提交。

RED（变异自证，见 docs/09 第九十八批）：
- 把批量取数改回逐条查询 → 用例 1 的三表 SELECT 数 3 → 36 → 红；
- 去掉 `commit_every` → 用例 2 的提交次数 5 → 1 → 红；
- 预取键退回 `(ref_id, dedup_key)` 两元组 → 用例 3 的"同场活动第二位家长"漏发 → 红。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.database import engine
from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay, _pay_deposit

KIDS = 12  # 同一家长下 12 个孩子，各借 1 本 → 12 条"即将到期"
BATCHED_SELECT_BUDGET = 3  # children + books + parents 各一次
FAKE_TODAY = date.today() + timedelta(days=30)  # 远离真实"今天"，避免真实时钟干扰
DUE_NODE_DAYS = 5


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


class _CommitCounter:
    """统计真实 COMMIT 次数（连接级事件：任务里每次 `db.commit()` 都计数）。"""

    def __init__(self):
        self.n = 0

    def __enter__(self):
        def _on_commit(conn):  # noqa: ANN001
            self.n += 1

        self._listener = _on_commit
        event.listen(engine, "commit", _on_commit)
        return self

    def __exit__(self, *exc):  # noqa: ANN002
        event.remove(engine, "commit", self._listener)
        return False


class _Clock(datetime):
    """可控时钟：`reminders` 内 `datetime.now()` 返回 `fixed`（不 sleep、可判定）。"""

    fixed: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return cls.fixed


def _due_borrows(client: TestClient, h: dict) -> tuple[int, int]:
    """1 家长 × 12 孩子 × 1 书目（12 副本）各借 1 本，到期日钉在"假今天 + 5 天"。

    返回 (parent_id, book_id)。
    """
    p, first_child, _mini = _family(client, h, "13988000001", "到期提醒孩0")
    child_ids = [first_child["id"]]
    for i in range(1, KIDS):
        c = client.post(
            f"/api/admin/members/parents/{p['id']}/children",
            json={"name": f"到期提醒孩{i}"},
            headers=h,
        )
        child_ids.append(c.json()["id"])
    for cid in child_ids:
        _pay(client, h, cid, "observation_fee")
        _pay_deposit(client, h, cid)

    book = client.post(
        "/api/admin/books",
        json={"isbn": None, "title": "到期提醒压测书", "word_count": 1000, "copy_count": KIDS},
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
        due = datetime.combine(FAKE_TODAY + timedelta(days=DUE_NODE_DAYS), datetime.min.time())
        for rec in rows:
            rec.due_at = due + timedelta(hours=10)  # 定时任务造态：有意直改库
        db.commit()
    return p["id"], book["id"]


def _run_due_remind(monkeypatch):
    """钉死时钟后跑一次到期提醒（直接调服务层：这样提交次数只含任务自己）。"""
    from backend.domain.circulation import reminders as reminders_mod

    _Clock.fixed = datetime.combine(FAKE_TODAY, datetime.min.time()) + timedelta(hours=10)
    monkeypatch.setattr(reminders_mod, "datetime", _Clock)
    db = _db()
    try:
        return reminders_mod.book_due_remind(db)
    finally:
        db.close()


def _due_notifs(parent_id: int) -> list:
    from backend.common.notification_models import Notification

    with _db() as db:
        return (
            db.query(Notification)
            .filter(
                Notification.parent_id == parent_id,
                Notification.scene == "borrow.due_remind",
                Notification.is_deleted == 0,
            )
            .all()
        )


# ---------- ① N+1：查询数与记录数解耦 ----------


def test_due_remind_query_count_decoupled_from_records(client: TestClient, monkeypatch):
    """12 条即将到期：全部提醒 + 三张表 SELECT ≤ 3（与记录数无关）。

    RED（变异）：批量取数改回逐条 `Child`/`Book`/`Parent` 查询 → 3 → 36 → 红。
    """
    h = _h(client)
    parent_id, _book_id = _due_borrows(client, h)

    with _SqlCounter(("children", "books", "parents")) as counter:
        sent = _run_due_remind(monkeypatch)

    assert sent == KIDS, f"{KIDS} 条都该提醒（实 {sent}）"
    notifs = _due_notifs(parent_id)
    assert len(notifs) == KIDS, f"{KIDS} 条家长通知（实 {len(notifs)}）"
    assert len(counter.hits) <= BATCHED_SELECT_BUDGET, (
        f"三张表的 SELECT 应 ≤ {BATCHED_SELECT_BUDGET}（批量取数），实 {len(counter.hits)}：{counter.hits}"
    )


def test_due_remind_is_idempotent_within_node(client: TestClient, monkeypatch):
    """同一节点同一周期连跑两次：第二次 0 条（预取已发键生效，不重复通知）。"""
    h = _h(client)
    parent_id, _book_id = _due_borrows(client, h)
    assert _run_due_remind(monkeypatch) == KIDS
    assert _run_due_remind(monkeypatch) == 0, "同节点重复跑不得重复提醒"
    assert len(_due_notifs(parent_id)) == KIDS


# ---------- ② 分块提交：不再整批单事务 ----------


def test_due_remind_commits_in_chunks(client: TestClient, monkeypatch):
    """12 条 + 粒度 3 → 任务内部提交 ≥ 4 次（整批单事务时会只有 1 次）。

    RED（变异）：去掉 `commit_every` 调用 → 提交次数 1 → 红。
    """
    from backend.common import batch as batch_mod

    h = _h(client)
    _due_borrows(client, h)
    monkeypatch.setattr(batch_mod, "BATCH_COMMIT_CHUNK", 3)

    with _CommitCounter() as commits:
        sent = _run_due_remind(monkeypatch)

    assert sent == KIDS, f"{KIDS} 条都该提醒（实 {sent}）"
    assert commits.n >= 4, (
        f"12 条按粒度 3 应至少提交 4 次（分块提交），实 {commits.n} 次——"
        "RED=整批单事务，持锁窗口覆盖全批"
    )


def test_commit_every_resolves_chunk_at_call_time(monkeypatch):
    """粒度常量在**调用时**解析（用例才能把粒度调小观测分块），边界语义：整除才提交。"""
    from backend.common import batch as batch_mod

    monkeypatch.setattr(batch_mod, "BATCH_COMMIT_CHUNK", 2)
    db = _db()
    try:
        assert batch_mod.commit_every(db, 1) is False
        assert batch_mod.commit_every(db, 2) is True, "缺省粒度必须在调用时取模块常量"
        assert batch_mod.commit_every(db, 4, 4) is True
        assert batch_mod.commit_every(db, 5, 4) is False
        assert batch_mod.commit_every(db, 0, 4) is False, "0 条不提交（没处理过）"
    finally:
        db.close()


# ---------- ③ 预取键口径：ref_id 不总是一对一 ----------


def test_activity_remind_notifies_second_parent_of_same_activity(client: TestClient):
    """**同场活动**的第二位家长必须也能收到提醒（预取键要带 parent_id）。

    场景（真实可发生）：首轮任务跑过之后才有家庭报名 → 第二轮跑时，
    "该活动该节点已有通知行"（第一位家长的）若被当成"已提醒过整场活动"，
    第二位家长就**永远收不到**提醒。

    RED（变异）：预取键退回 `(ref_id, dedup_key)` 两元组 → 第二轮第二位家长漏发 → 红。
    """
    from backend.common.notification_models import Notification

    h = _h(client)
    fam_a = _family(client, h, "13988000011", "活动提醒孩A")
    fam_b = _family(client, h, "13988000012", "活动提醒孩B")
    for _p, c, _mini in (fam_a, fam_b):
        _pay(client, h, c["id"], "observation_fee")

    act = client.post(
        "/api/admin/activities",
        json={
            "title": "同场活动提醒",
            "activity_type": "book_club",
            "start_at": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
            "location": "馆内",
            "max_quota": 10,
            "fee": 0,
            "description": "",
            "member_only": True,
        },
        headers=h,
    ).json()

    # 首轮：只有 A 家报名 → 提醒发到 A
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": fam_a[1]["id"]},
        headers=fam_a[2],
    )
    assert r.status_code == 200, r.text
    assert (
        client.post("/api/admin/tasks/activity_remind/run", headers=h).json()["status"] == "success"
    )

    def _remind_notifs(parent_id: int) -> list:
        with _db() as db:
            return (
                db.query(Notification)
                .filter(
                    Notification.parent_id == parent_id,
                    Notification.scene == "activity.remind",
                    Notification.is_deleted == 0,
                )
                .all()
            )

    assert len(_remind_notifs(fam_a[0]["id"])) == 1, "A 家首轮应收到 1 条"

    # 次轮：B 家也报名（活动尚在提醒窗口内）→ B 家必须收到
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll",
        json={"child_id": fam_b[1]["id"]},
        headers=fam_b[2],
    )
    assert r.status_code == 200, r.text
    assert (
        client.post("/api/admin/tasks/activity_remind/run", headers=h).json()["status"] == "success"
    )

    a_notifs, b_notifs = _remind_notifs(fam_a[0]["id"]), _remind_notifs(fam_b[0]["id"])
    assert len(a_notifs) == 1, f"A 家不得重复提醒（实 {len(a_notifs)} 条）"
    assert len(b_notifs) == 1, (
        f"同场活动的第二位家长必须也收到提醒（实 {len(b_notifs)} 条）——"
        "RED=预取键只按 (ref_id, dedup_key) 判重，把'别的家长已发过'当成'整场已发过'"
    )
