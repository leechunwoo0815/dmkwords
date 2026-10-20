# tests/unit/test_audit8_d_perf_20261020.py — 第八轮审查 D 批：性能回归守护（P2-39）
"""**缺陷（第八轮审查 P2-39）**：活动列表 **N+1** —— `list_admin` / `list_upcoming` 每行调一次
`_quota_used(a.id)` 做 COUNT（管理端 200 行 = **201 条 SQL**、小程序 50 行 = 51 条）。
大屏轮询 / 翻页每次都付一遍。

**修法**：新增 `_quota_used_map(ids)`（一次 GROUP BY），`_activity_view` 接受预算好的名额数；
单行路径（`detail`）仍走 `_quota_used`。

**判据（行为，真数 SQL 条数）**：对 3 个活动与 20 个活动各跑一次列表，**SELECT 条数必须相同**。
RED（还原修复）：条数随行数线性增长（差 17 条）。

> P2-36/37/38 的索引由 **`alembic check` 机械守护**：索引声明写在模型 `__table_args__` 里，
> 谁把它删掉 → autogenerate 报 diff → 门禁第 [6] 步红（无需另写用例）。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.database import SessionLocal, engine
from backend.domain.activity.models import Activity
from backend.domain.activity.service import ActivityService


def _make_activities(db, n: int) -> None:
    for i in range(n):
        db.add(
            Activity(
                title=f"N+1 守护 {i}",
                activity_type="book_club",
                start_at=datetime.now() + timedelta(days=30),
                location="馆内",
                max_quota=10,
                fee=0,
                status=Activity.STATUS_PUBLISHED,
            )
        )
    db.commit()


class _SqlCounter:
    """数 SELECT 条数（只数读，写不算——建数据本身有写）。"""

    def __init__(self):
        self.n = 0

    def __enter__(self):
        self._fn = lambda *a, **k: None

        def _hook(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                self.n += 1

        self._hook = _hook
        event.listen(engine, "before_cursor_execute", _hook)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._hook)
        return False


def test_activity_lists_have_no_n_plus_one():
    """列表 SQL 条数**与行数无关**（管理端 / 小程序两条路径各验一次）。"""
    db = SessionLocal()
    try:
        svc = ActivityService(db)
        _make_activities(db, 3)
        with _SqlCounter() as c3:
            admin3, up3 = len(svc.list_admin()), len(svc.list_upcoming())
        _make_activities(db, 17)  # 共 20 个
        with _SqlCounter() as c20:
            admin20, up20 = len(svc.list_admin()), len(svc.list_upcoming())

        assert admin20 > admin3, "前置失败：活动没建进去"
        assert c20.n == c3.n, (
            f"活动列表出现 N+1：3 行时 {c3.n} 条 SELECT，20 行时 {c20.n} 条（应相同）"
        )
        assert c20.n <= 4, f"两条列表路径合计应 ≤4 条 SELECT（实测 {c20.n}）"
        assert up20 >= up3
    finally:
        db.close()


def test_activity_detail_quota_is_correct(client: TestClient):
    """对照组（**防止修过头**）：批量改成一次 GROUP BY 后，名额数字仍必须准确。

    建 1 个活动 + 1 个孩子报名 → `list_admin` 与 `detail` 两个口径都要报 `quota_used=1`。
    """
    from tests.unit.test_wm9_activity import _h, _mk_activity, _mk_child

    h = _h(client)
    act = _mk_activity(client, h, quota=5, fee=0, title="名额口径守护")
    c, mini = _mk_child(client, h, "13981400001", "名额孩")
    r = client.post(
        f"/api/miniapp/activities/{act['id']}/enroll", json={"child_id": c["id"]}, headers=mini
    )
    assert r.status_code == 200, r.text

    rows = client.get("/api/admin/activities", headers=h).json()
    row = next(x for x in rows if x["id"] == act["id"])
    assert row["quota_used"] == 1 and row["quota_left"] == 4, f"列表口径错：{row}"

    detail = client.get(f"/api/admin/activities/{act['id']}", headers=h).json()
    assert detail["quota_used"] == 1, f"详情口径错（批量与单查必须同源）：{detail}"
