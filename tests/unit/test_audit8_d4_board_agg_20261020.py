# tests/unit/test_audit8_d4_board_agg_20261020.py — 第八轮大审查 D 批（下）P2-40
"""榜单/横幅聚合：**覆盖索引**（消回表）+ 横幅**不再每个信息流请求重算**。

实测口径（12 万条词账造量 + EXPLAIN ANALYZE，60 个孩子，同库同数据）：
- 修复前：`Index lookup on words_ledgers using uq_words_child_book` + 回表过滤
  `is_deleted=0` → 聚合 `actual time=1.74..68.3`，端到端 **49.2ms**；
- 修复后：`Covering index lookup on words_ledgers using ix_words_ledgers_board_agg
  (child_id=..., is_deleted=0)` → 聚合 `0.448..25.4`，端到端 **18.4ms**。

横幅（本周全馆共读词数）是**全馆周聚合**，原先在 `list_posts`（信息流每个请求）上都重算
一次；现在进 60 秒 TTL 缓存（展示型统计，秒级陈旧无感；口径仍是同一份 `period_entries`，
不是第二套算法——计数同源纪律）。

RED（变异自证）：
- 去掉覆盖索引（迁移/模型）→ 用例 1 红（索引不存在/列序不对）；
- 去掉横幅缓存 → 用例 2 红（两个请求算出两次全馆周聚合）。
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import engine
from tests.unit.test_wm14_circle import (
    _credit_words_at,
    _db,
    _h,
    _mk_parent_with_child,
    _monday_of,
    _seed_book,
)

INDEX_NAME = "ix_words_ledgers_board_agg"
EXPECT_COLUMNS = ["child_id", "is_deleted", "created_at", "word_count"]


def test_board_agg_index_present_with_exact_column_order():
    """覆盖索引必须在库上真实存在且**列序**正确（列序错了就又要回表）。

    这条同时钉住"迁移与模型声明同源"（模型侧由 `alembic check` 兜底）。
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.statistics"
                " WHERE table_schema = DATABASE() AND table_name = 'words_ledgers'"
                " AND index_name = :ix ORDER BY seq_in_index"
            ),
            {"ix": INDEX_NAME},
        ).fetchall()
    cols = [r[0] for r in rows]
    assert cols == EXPECT_COLUMNS, (
        f"覆盖索引 {INDEX_NAME} 列序不对（join 键/过滤列/时间窗/求和列必须按序在索引里）：{cols}"
        "——RED=去掉索引或改错列序，聚合退回『索引查找 + 回表过滤』"
    )


def test_banner_not_recomputed_on_every_feed_request(client: TestClient, monkeypatch):
    """信息流连续两次请求 → 全馆周聚合只算**一次**（第二次命中 TTL 缓存）。"""
    from backend.domain.growth.board_service import LeaderboardService
    from backend.domain.reading_circle import service as circle_service

    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13990000001", "横幅缓存孩", "CacheKid")
    b1 = _seed_book(client, h, "9788400000958", 600)
    _credit_words_at(c1, b1, 600, datetime.now())  # 本周
    circle_service.invalidate_banner_cache()

    calls = {"n": 0}
    real = LeaderboardService.period_entries

    def _counting(self, start, end=None):  # noqa: ANN001
        calls["n"] += 1
        return real(self, start, end)

    monkeypatch.setattr(LeaderboardService, "period_entries", _counting)

    first = client.get("/api/miniapp/circle/posts", headers=m1).json()
    assert calls["n"] == 1, f"首次请求应算一次全馆周聚合（实 {calls['n']}）"
    second = client.get("/api/miniapp/circle/posts", headers=m1).json()
    assert calls["n"] == 1, (
        f"第二次请求必须命中缓存（实算了 {calls['n']} 次）——"
        "RED=横幅在**每个信息流请求**上重算全馆周聚合（随词账流水线性增长）"
    )
    assert first["banner"] == second["banner"], "缓存不得改变口径"

    # 口径未变：横幅值仍等于 period_entries 的实时结果（同一份算法，不是第二套聚合）
    with _db() as db:
        monday = datetime.combine(_monday_of(), datetime.min.time())
        entries = LeaderboardService(db).period_entries(monday)
        expect = {"words": sum(e["words"] for e in entries), "kids": len(entries)}
    assert second["banner"] == expect, f"横幅口径必须与周榜同源：{second['banner']} != {expect}"


def test_banner_cache_invalidation_takes_effect(client: TestClient):
    """清缓存后立刻反映新数据（测试夹具与"数据变更要马上看到"的场景走这条）。"""
    from backend.domain.reading_circle import service as circle_service

    h = _h(client)
    c1, m1 = _mk_parent_with_child(client, h, "13990000002", "横幅失效孩", "FreshKid")
    b1 = _seed_book(client, h, "9788400000959", 700)

    circle_service.invalidate_banner_cache()
    before = client.get("/api/miniapp/circle/posts", headers=m1).json()["banner"]
    assert before["kids"] == 0, f"尚无词账时不应有在坚持的孩子：{before}"

    _credit_words_at(c1, b1, 700, datetime.now())
    cached = client.get("/api/miniapp/circle/posts", headers=m1).json()["banner"]
    assert cached == before, "缓存期内允许秒级陈旧（这是缓存的语义）"

    circle_service.invalidate_banner_cache()
    after = client.get("/api/miniapp/circle/posts", headers=m1).json()["banner"]
    assert after["words"] >= 700 and after["kids"] >= 1, f"清缓存后必须反映新词账：{after}"
