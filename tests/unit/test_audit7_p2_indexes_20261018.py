# tests/unit/test_audit7_p2_indexes_20261018.py — 第七轮 P2-2/P2-4：生产规模索引
"""两条索引的**常驻回归**：书城/书目列表（P2-4）与待办墙（P2-2）。

为什么必须造量：演示库 35 本书、5 条待办时优化器怎么选都对，**看不出问题**；
审查报告实测的就是"造量后才暴露的扫描/排序"。本用例造 3000 行再断言执行计划。

RED（去掉索引，或把索引列尾从 `id` 改成 `create_time`）：
  · 待办墙回到 `type=ALL … Using filesort`；· 书城列表回到 `key=PRIMARY`（扫描+过滤）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from tests.unit.test_p1_audit_20261015 import _db

N = 3000


def _seed(db) -> None:
    """造量：1/3 上架、1/7 软删的书；1/9 软删的待办（选择性足够，索引才有意义）。"""
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.catalog.models import Book

    now = datetime.now()
    db.execute(
        insert(Book),
        [
            {
                "title": f"索引用例书 {i}",
                "author": "idx",
                "word_count": 100 + i,
                "status": 1 if i % 3 == 0 else 0,
                "is_deleted": 1 if i % 7 == 0 else 0,
                "create_time": now - timedelta(minutes=i),
                "update_time": now,
            }
            for i in range(N)
        ],
    )
    db.execute(
        insert(AdminNotification),
        [
            {
                "scene": "idx.scene",
                "title": f"索引用例待办 {i}",
                "content": "idx",
                "ref_type": "child",
                "ref_id": str(i),
                "dedup_key": str(i),
                "is_deleted": 1 if i % 9 == 0 else 0,
                "created_at": now - timedelta(minutes=i),
            }
            for i in range(N)
        ],
    )
    db.commit()


def _plan(db, sql: str) -> dict:
    row = db.execute(text("EXPLAIN " + sql)).mappings().fetchall()[0]
    return {"type": row["type"], "key": row["key"], "extra": row["Extra"]}


def _index_names(db, table: str) -> set[str]:
    rows = db.execute(text(f"SHOW INDEX FROM {table}")).mappings().fetchall()
    return {r["Key_name"] for r in rows}


def test_indexes_exist():
    """两条索引必须真实存在于库（DBA 手工删索引 → 红）。"""
    with _db() as db:
        assert "ix_books_deleted_status_id" in _index_names(db, "books"), sorted(
            _index_names(db, "books")
        )
        assert "ix_admin_notif_deleted_created" in _index_names(db, "admin_notifications"), sorted(
            _index_names(db, "admin_notifications")
        )


def test_book_mall_list_plan_uses_index_without_filesort(client: TestClient):
    """书城首屏：`is_deleted=0 AND status=1 ORDER BY id DESC LIMIT 20` 走复合索引、无 filesort。"""
    with _db() as db:
        _seed(db)
        plan = _plan(
            db,
            "SELECT * FROM books WHERE is_deleted=0 AND status=1 ORDER BY id DESC LIMIT 20",
        )
    assert plan["key"] == "ix_books_deleted_status_id", plan
    assert "filesort" not in plan["extra"].lower(), plan


def test_todo_wall_plan_uses_index_without_filesort(client: TestClient):
    """待办墙：`is_deleted=0 ORDER BY created_at DESC, id DESC` 走复合索引、**不再全表扫+排序**。"""
    with _db() as db:
        _seed(db)
        plan = _plan(
            db,
            "SELECT * FROM admin_notifications WHERE is_deleted=0"
            " ORDER BY created_at DESC, id DESC LIMIT 20",
        )
    assert plan["key"] == "ix_admin_notif_deleted_created", plan
    assert plan["type"] != "ALL", plan
    assert "filesort" not in plan["extra"].lower(), plan
