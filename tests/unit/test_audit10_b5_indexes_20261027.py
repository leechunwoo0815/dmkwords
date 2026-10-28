# tests/unit/test_audit10_b5_indexes_20261027.py — 第十轮 W3-08 通知预取索引回归
"""通知去重预取索引的**常驻回归**（B5 批）。

为什么必须造量：演示库 34 条通知下任何计划都是毫秒级；造到数千条、混入多场景后，
只命中 `ix_notifications_scene(scene)` 的旧计划要取回大半个 scene 再逐行过滤。

RED（去掉索引）：`key` 退回 `ix_notifications_scene`、扫描行数上升（本用例断言
复合索引名 + 扫描行数 ≤1000）。

同批否掉的 W3-06（books「全部」页）**没有**加索引：60k 造量实测，filesort 只在
统计未刷新时出现，`ANALYZE TABLE books` 后一律 PRIMARY 逆序扫描（40 行）——
有无 (is_deleted,id) 计划相同，索引从未被选中。口径见
`backend/domain/catalog/models.py` 的注释与 `docs/09` 第一百三十五批。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import insert, text

from tests.unit.test_p1_audit_20261015 import _db

N = 8000


def _seed(db) -> None:
    """造量：通知 scene 混合（否则单场景数据下索引前缀区分度不可见）。"""
    from backend.common.notification_models import Notification

    now = datetime.now()
    db.execute(
        insert(Notification),
        [
            {
                "scene": "borrow.due" if i % 6 == 0 else "quiz.pass",
                "title": f"B5 索引通知 {i}",
                "content": "idx",
                "ref_type": "borrow",
                "ref_id": str(i),
                "dedup_key": str(i),
                "parent_id": i % 300 + 1,
                "is_deleted": 1 if i % 9 == 0 else 0,
                "create_time": now - timedelta(minutes=i),
                "update_time": now,
            }
            for i in range(N)
        ],
    )
    db.commit()


def _plan(db, sql: str) -> dict:
    row = db.execute(text("EXPLAIN " + sql)).mappings().fetchall()[0]
    return {"type": row["type"], "key": row["key"], "extra": row["Extra"], "rows": row["rows"]}


def _index_names(db, table: str) -> set[str]:
    rows = db.execute(text(f"SHOW INDEX FROM {table}")).mappings().fetchall()
    return {r["Key_name"] for r in rows}


def test_notification_prefetch_index_exists():
    """索引必须真实存在于库（DBA 手工删索引 → 红）。"""
    with _db() as db:
        assert "ix_notifications_scene_ref" in _index_names(db, "notifications"), sorted(
            _index_names(db, "notifications")
        )


def test_notification_prefetch_plan_uses_composite():
    """通知预取：scene + ref_type + ref_id IN 走复合索引（不是只命中 scene 单列）。"""
    with _db() as db:
        _seed(db)
        ids = ",".join(f"'{i}'" for i in range(1, 51))
        plan = _plan(
            db,
            "SELECT ref_id, dedup_key, parent_id FROM notifications"
            f" WHERE scene='borrow.due' AND ref_type='borrow' AND ref_id IN ({ids})"
            " AND is_deleted=0",
        )
    assert plan["key"] == "ix_notifications_scene_ref", plan
    assert plan["type"] != "ALL", plan
    assert plan["rows"] <= 1000, plan
