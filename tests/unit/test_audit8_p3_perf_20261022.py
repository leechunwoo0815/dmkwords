# tests/unit/test_audit8_p3_perf_20261022.py — 第八轮 P3 性能族（第一百一十五批）
"""**缺陷（第八轮审查 §3.3 性能 7 条）**——逐条对应：

| # | 缺陷 | 判据（本文件） |
|---|---|---|
| 1 | `quiz_status_batch` 每题一次 `MAX(total_questions)` | `test_quiz_status_batch_query_count_is_constant` |
| 2 | 家长列表每行 `has_orders` COUNT（1+N） | `test_parent_list_query_count_is_constant` |
| 3 | 阅读圈管理列表 1+2N（已 JOIN 的 Child/Parent 被丢弃后重查） | `test_circle_admin_list_query_count_is_constant` |
| 4 | 点赞头像墙**无 SQL 上限**（热帖全量拉回 Python 截断） | `test_likers_map_fetches_at_most_limit_per_post` |
| 5 | 仪表盘 21 COUNT + 配置名查询（≈27 SQL/次）无缓存 | `test_dashboard_overview_is_cached` |
| 6 | 报告区间 Python 求和（整段行拉回） | `test_report_range_summary_query_count_is_constant` |
| 7 | `refund_requests.order_id` 无索引 | **不另写用例**：索引声明写在模型（`index=True`）→ `alembic check` 机械守护（D 批 P2-36/37/38 先例）；落地另有 `SHOW INDEX` 实测 |

**判据一律是"SQL 条数/取回行数"，不是耗时**（耗时随机器抖动，做不了可证伪断言；红线 171 族）。
RED（还原修复）：条数随行数线性增长（1→10 行时 +9 条 / 2→20 帖时 +38 条 / 30 个赞全量取回）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import event

from backend.database import SessionLocal, engine


class _SqlCounter:
    """数 SELECT 条数（与 `test_audit8_d_perf_20261020.py` 同法）。"""

    def __init__(self):
        self.n = 0

    def __enter__(self):
        def _hook(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                self.n += 1

        self._hook = _hook
        event.listen(engine, "before_cursor_execute", _hook)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._hook)
        return False


class _RowCounter:
    """数 SELECT **取回行数**（用 `cursor.rowcount`；MySQL 缓冲游标下 SELECT 的 rowcount = 行数）。"""

    def __init__(self):
        self.rows = 0
        self.statements: list[str] = []

    def __enter__(self):
        def _hook(conn, cursor, statement, params, context, executemany):
            if statement.strip().upper().startswith("SELECT"):
                rc = getattr(cursor, "rowcount", -1)
                self.rows += max(rc, 0)
                self.statements.append(statement)

        self._hook = _hook
        event.listen(engine, "after_cursor_execute", _hook)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "after_cursor_execute", self._hook)
        return False


def _child(db, phone: str, name: str):
    """家长 + 孩子（最小可用夹具）。"""
    from backend.domain.identity.models import Child, Parent

    p = Parent(name="性能家长", phone=phone)
    db.add(p)
    db.flush()
    c = Child(name=name, parent_id=p.id)
    db.add(c)
    db.commit()
    return p, c


_ISBN_SEQ = [0]


def _books(db, n: int) -> list[int]:
    """造 n 本书（ISBN 全局自增，同一用例内多次调用不撞唯一索引）。"""
    from backend.domain.catalog.models import Book

    ids = []
    for _ in range(n):
        _ISBN_SEQ[0] += 1
        b = Book(
            title=f"性能守护书 {_ISBN_SEQ[0]}",
            isbn=f"978{_ISBN_SEQ[0]:010d}",
            word_count=1000,
            status=1,
        )
        db.add(b)
        db.flush()
        ids.append(b.id)
    db.commit()
    return ids


# ---------- ① quiz_status_batch ----------


def test_quiz_status_batch_query_count_is_constant():
    """书架批量测验状态：SQL 条数**与书数无关**（原先逐书一次 MAX → 上限 50 条）。

    RED（还原修复：`total_map` 字典推导逐书查）：2 本书 3 条、12 本书 13 条 → 断言红。
    """
    from backend.domain.growth.quiz_batch import quiz_status_batch

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009001", "性能孩A")
        few = _books(db, 2)
        many = _books(db, 12)
        # **必须有测验记录**：否则 `attempt_rows` 为空 → N+1 那支根本不被走到
        # （首版夹具漏了这步，变异自证当场判"绿✗ 未被抓住"——自证的价值）
        from backend.domain.growth.models import QuizAttempt

        for bid in few + many:
            db.add(
                QuizAttempt(
                    child_id=child.id,
                    book_id=bid,
                    score=4,
                    total_questions=5,
                    passed=1,
                    snapshot="[]",
                )
            )
        db.commit()
        # 预热一次（identity map 变温）——否则"首次调用多几条"会被误判成 N+1
        quiz_status_batch(db, child, few)
        with _SqlCounter() as c1:
            quiz_status_batch(db, child, few)
        with _SqlCounter() as c2:
            quiz_status_batch(db, child, many)
        assert c1.n == c2.n, f"2 本书 {c1.n} 条 vs 12 本书 {c2.n} 条（N+1 未收口）"
        assert c2.n <= 5, f"12 本书竟要 {c2.n} 条 SQL（应 ≤5：3 条批查 + 会话开销）"
    finally:
        db.close()


# ---------- ② 家长列表 ----------


def test_parent_list_query_count_is_constant():
    """家长管理列表：SQL 条数**与行数无关**（原先每行一次 has_orders COUNT）。

    RED（还原修复：逐行 `has_orders`）：3 行 3 条 COUNT、21 行 21 条 → 断言红。
    """
    from backend.domain.identity.service import ParentService

    db = SessionLocal()
    try:
        for i in range(20):
            _child(db, f"139000091{i:02d}", f"性能孩{i}")
        svc = ParentService(db)
        svc.list_page(1, 3, None)  # 预热（同上）
        with _SqlCounter() as c1:
            svc.list_page(1, 3, None)
        with _SqlCounter() as c2:
            svc.list_page(1, 20, None)
        assert c1.n == c2.n, f"3 行 {c1.n} 条 vs 20 行 {c2.n} 条（has_orders N+1 未收口）"
        assert c2.n <= 6, (
            f"20 行竟要 {c2.n} 条 SQL（应 ≤6：分页 + children_count + has_orders_map）"
        )
    finally:
        db.close()


# ---------- ③ 阅读圈管理列表 ----------


def test_circle_admin_list_query_count_is_constant():
    """阅读圈管理列表：SQL 条数**与帖数无关**（原先每帖各查 Child/Parent = 1+2N）。

    RED（还原修复：`_admin_post_view` 内逐帖查）：2 帖 5 条、20 帖 41 条 → 断言红。
    """
    from backend.domain.reading_circle.admin_service import AdminCircleService
    from backend.domain.reading_circle.models import CirclePost

    db = SessionLocal()
    try:
        p, c = _child(db, "13900009201", "性能孩圈")
        for i in range(20):
            db.add(
                CirclePost(
                    child_id=c.id,
                    parent_id=p.id,
                    card_type="milestone",
                    ref_id=1000 + i,  # 必填（成就关联 id；本用例只关心列表查询条数）
                    card_data="{}",
                )
            )
        db.commit()
        svc = AdminCircleService(db)
        svc.list_posts(1, 2)  # 预热（同上）
        with _SqlCounter() as c1:
            svc.list_posts(1, 2)
        with _SqlCounter() as c2:
            svc.list_posts(1, 20)
        assert c1.n == c2.n, f"2 帖 {c1.n} 条 vs 20 帖 {c2.n} 条（1+2N 未收口）"
        assert c2.n <= 6, f"20 帖竟要 {c2.n} 条 SQL（应 ≤6：count + 分页 JOIN + 概览）"
    finally:
        db.close()


# ---------- ④ 点赞头像墙 ----------


def test_likers_map_fetches_at_most_limit_per_post():
    """点赞墙：**取回行数 ≤ 帖数 × limit**（原先该页全部点赞行都拉回来）。

    RED（还原修复：无 SQL 上限、Python 截断）：1 帖 30 个赞 + limit=8 → 取回 30 行 → 断言红。
    """
    from backend.domain.reading_circle.models import CircleLike, CirclePost
    from backend.domain.reading_circle.service import CircleService

    db = SessionLocal()
    try:
        p, c = _child(db, "13900009301", "性能孩赞")
        post = CirclePost(
            child_id=c.id, parent_id=p.id, card_type="milestone", ref_id=2001, card_data="{}"
        )
        db.add(post)
        db.flush()
        for i in range(30):
            _, liker = _child(db, f"1390000931{i:01d}", f"点赞孩{i}")
            db.add(CircleLike(post_id=post.id, child_id=liker.id, parent_id=liker.parent_id))
        db.commit()

        with _RowCounter() as rc:
            buckets = CircleService(db)._likers_map([post.id], limit=8)
        assert len(buckets[post.id]) == 8, f"每帖最多 8 条，实得 {len(buckets[post.id])}"
        assert rc.rows <= 12, (
            f"取回 {rc.rows} 行——SQL 层未按帖限条（应 ≤12：8 行点赞 + 等级/孩子等小额查询）"
        )
    finally:
        db.close()


# ---------- ⑤ 仪表盘缓存 ----------


def test_dashboard_overview_is_cached():
    """仪表盘总览走 30s TTL 缓存：第二次调用 **0 条 SQL**。

    RED（还原修复：无缓存）：两次调用各约 27 条 SQL → 第二次不为 0 → 断言红。
    """
    from backend.domain.admin.service import DashboardService, invalidate_dashboard_cache

    db = SessionLocal()
    try:
        invalidate_dashboard_cache()
        svc = DashboardService(db)
        with _SqlCounter() as c1:
            first = svc.overview()
        with _SqlCounter() as c2:
            second = svc.overview()
        assert c1.n > 0, "首次调用必须有真实查询"
        assert c2.n == 0, f"命中缓存应为 0 条 SQL，实得 {c2.n} 条"
        assert first == second, "缓存命中必须与首次结果一致"
        invalidate_dashboard_cache()
        with _SqlCounter() as c3:
            svc.overview()
        assert c3.n > 0, "失效后必须重新算（invalidate_dashboard_cache 未生效）"
    finally:
        invalidate_dashboard_cache()
        db.close()


# ---------- ⑥ 报告区间聚合 ----------


def test_report_range_summary_query_count_is_constant():
    """报告区间汇总：SQL 条数**与词账行数无关**（原先整段拉回 Python len/sum）。

    RED（还原修复：`.all()` + Python 求和）：仍是固定条数——**故本用例以"取回行数"为判据**：
    50 行词账时取回行数必须远小于 50（聚合在 SQL 侧完成）。
    """
    from backend.domain.growth.models import WordsLedger
    from backend.domain.growth.report_service import ReportService

    db = SessionLocal()
    try:
        _, child = _child(db, "13900009401", "性能孩报告")
        book_ids = _books(db, 50)
        for bid in book_ids:
            db.add(WordsLedger(child_id=child.id, book_id=bid, word_count=100))
        db.commit()

        start = datetime.now() - timedelta(days=1)
        end = datetime.now() + timedelta(days=1)
        with _RowCounter() as rc:
            summary = ReportService(db).range_summary(child, start, end)
        assert summary["books"] == 50, f"本数口径错：{summary['books']}（应为词账行数 50）"
        assert summary["words"] == 5000, f"词数口径错：{summary['words']}"
        assert rc.rows <= 12, f"取回 {rc.rows} 行——聚合未下推 SQL（50 行词账不该整段拉回）"
    finally:
        db.close()
