# tests/unit/test_a3b2_perf_20261011.py — A3 第二批：三处 N+1 + 计数竞态（2026-10-11）
"""实测口径（`before_cursor_execute` 事件计数，与 `test_refund_admin_list_sql_count_is_constant` 同法）：

| 端点 | 修复前 | 修复后 |
|---|---|---|
| `GET /api/miniapp/circle/posts`（10 帖） | **39 条**（每帖 2 条孩子/家长 + 1 条点赞数） | **12 条** |
| `GET /api/admin/orders`（20 行） | **24 条**（每行 2 条孩子/家长） | **6 条** |
| `GET /api/admin/todo-counts` | 逐行 `resolve_many([n])`（行数增长则线性劣化） | 整批 resolve 一次（**与行数无关**） |

另：`lookup_count` 读改写 → 原子 UPDATE（并发查同一词不再丢计数）。
"""

from __future__ import annotations

from sqlalchemy import event, text

from tests.unit.test_wm10_concurrency import _db, _family, _h

#: 固定上限（不是"刚好等于"——避免把合法的小幅变化判红；超标说明又长出 N+1）
CIRCLE_FEED_MAX_SQL = 18
ADMIN_ORDERS_MAX_SQL = 10


def _count_sql(fn) -> int:
    from backend.database import engine

    stmts: list[str] = []

    def rec(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        stmts.append(statement)

    event.listen(engine, "before_cursor_execute", rec)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", rec)
    return len(stmts)


def _seed_posts(parent_id: int, child_id: int, n: int) -> None:
    """直接落库造帖（渲染卡片图对"数 SQL"无意义，跳过）。"""
    from backend.domain.reading_circle.models import CirclePost

    with _db() as s:
        for i in range(n):
            s.add(
                CirclePost(
                    parent_id=parent_id,
                    child_id=child_id,
                    card_type="words",
                    ref_id=i,
                    card_data="{}",
                )
            )
        s.commit()


def test_circle_feed_sql_is_constant(client, db):
    """信息流每页 SQL 条数与帖子数无关（修复前 10 帖 39 条：每帖孩子/家长/点赞数各一条）。"""
    h = _h(client)
    parent, child, mini = _family(client, h, "13981037411", name="小明")
    _seed_posts(parent["id"], child["id"], 10)

    n10 = _count_sql(
        lambda: client.get(
            "/api/miniapp/circle/posts", params={"page": 1, "page_size": 10}, headers=mini
        )
    )
    n3 = _count_sql(
        lambda: client.get(
            "/api/miniapp/circle/posts", params={"page": 1, "page_size": 3}, headers=mini
        )
    )
    assert n10 <= CIRCLE_FEED_MAX_SQL, f"信息流 10 帖 SQL={n10}（上限 {CIRCLE_FEED_MAX_SQL}）"
    assert n10 - n3 <= 2, f"条数随帖子数增长（10 帖 {n10} vs 3 帖 {n3}）——疑似 N+1 回流"


def test_admin_orders_sql_is_constant(client, db):
    """管理端订单列表 SQL 条数与行数无关（修复前 20 行 24 条：每行 2 条孩子/家长）。"""
    h = _h(client)
    parent, child, _mini = _family(client, h, "13981037412", name="小明")
    with _db() as s:
        for i in range(20):
            s.execute(
                text(
                    "INSERT INTO orders (order_no, order_type, parent_id, child_id, amount, status, "
                    "remark, is_deleted, create_time, update_time) VALUES (:no, 'custom', :p, :c, "
                    "1.00, 'paid', '', 0, NOW(), NOW())"
                ),
                {"no": f"DMKA3{i:04d}", "p": parent["id"], "c": child["id"]},
            )
        s.commit()
    n = _count_sql(
        lambda: client.get("/api/admin/orders", params={"page": 1, "page_size": 20}, headers=h)
    )
    assert n <= ADMIN_ORDERS_MAX_SQL, f"20 行订单列表 SQL={n}（上限 {ADMIN_ORDERS_MAX_SQL}）"


def test_todo_counts_sql_does_not_grow_with_rows(client, db):
    """待办计数：通知行数增加后 SQL 条数不变（原先逐行 resolve_many）。

    ⚠️ 如实标注：本用例在**演示量级**（6 行）下是"防回流"守卫，不是强复现——
    修复前的线性劣化要几十行才明显（见 `docs/审查报告-全量-2026-10-09.md` §四 P2-2）。
    """
    h = _h(client)
    before = _count_sql(lambda: client.get("/api/admin/todo-counts", headers=h))

    with _db() as s:
        for i in range(6):
            s.execute(
                text(
                    # B9（W3-09）：三列已 NOT NULL——裸 INSERT 必须自带时间戳（漏列会响亮报错）
                    "INSERT INTO admin_notifications (scene, title, content, ref_type, ref_id, "
                    "applicant_name, amount, dedup_key, is_deleted, created_at, create_time, "
                    "update_time) VALUES "
                    "('refund_request', :t, :c, 'refund_request', :r, 'A3 家长', 100.00, :d, 0, "
                    "NOW(), NOW(), NOW())"
                ),
                {"t": f"A3 待办#{i}", "c": f"内容{i}", "r": str(i), "d": f"a3-{i}"},
            )
        s.commit()

    after = _count_sql(lambda: client.get("/api/admin/todo-counts", headers=h))
    assert after <= before + 2, (
        f"通知行数增加后 SQL 从 {before} 涨到 {after}（疑似逐行 resolve 回流）"
    )


def test_lookup_count_is_atomic(client, db):
    """查词计数走原子 UPDATE（顺序三次查询即 ≥3；并发丢计数的强复现需多线程，此处为守卫）。"""
    h = _h(client)
    _p, child, mini = _family(client, h, "13981037401", name="小明")
    from backend.domain.identity.models import Child

    with _db() as s:
        kid = s.query(Child).filter(Child.id == child["id"]).first()
        kid.member_status = "formal"
        kid.member_start = __import__("datetime").date.today()
        kid.member_expire = __import__("datetime").date.today() + __import__("datetime").timedelta(
            days=200
        )
        s.commit()

    word = "apple"
    for _ in range(3):
        r = client.get(
            "/api/miniapp/vocabulary/lookup",
            params={"child_id": child["id"], "word": word},
            headers=mini,
        )
        assert r.status_code == 200, r.text

    with _db() as s:
        row = s.execute(
            text("SELECT lookup_count FROM vocabularies WHERE child_id=:c AND word=:w"),
            {"c": child["id"], "w": word},
        ).fetchone()
    assert row is not None and row[0] >= 3, f"三次查词后计数应 ≥3，实 {row[0] if row else None}"
