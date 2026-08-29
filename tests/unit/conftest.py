# tests/unit/conftest.py — WM1 测试夹具（真实 MySQL + 真实 HTTP 层）
"""反假绿纪律：测试走真实 MySQL（TRUNCATE 隔离）+ TestClient（真实 ASGI/HTTP 栈）。"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import SessionLocal, engine
from backend.domain.admin.service import invalidate_config_cache
from backend.main import app

ADMIN_TABLES = [
    "notifications",
    "task_run_logs",
    "dead_letters",
    "observation_reports",
    "transfer_requests",
    "withdrawal_requests",
    "refund_requests",
    "activity_enrollments",
    "activities",
    "vocabularies",
    "favorites",
    # dictionary_words 不清（2026-08-29 用户裁定）：340 万词库是基础数据，
    # 清了要重导 8 分钟；seed_dictionary 幂等补演示词，lookup 测试自洽
    "checkin_streak_awards",
    "milestone_awards",
    "child_growth_states",
    "point_ledgers",
    "words_ledgers",
    "quiz_attempts",
    "quiz_questions",
    "book_copies",
    "books",
    "reservations",
    "checkins",
    "reading_progress",
    "borrow_records",
    "deposit_ledgers",
    "deposits",
    "orders",
    "children",
    "parents",
    "audit_logs",
    "system_configs",
    "admin_users",
]


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试独立数据：截断 admin 三表 → 重播种子 → 清配置缓存。"""
    with engine.begin() as conn:
        # 2026-08-24 事故加固：dev.sh 后端与 pytest 共库，后端连接持有表锁会导致
        # TRUNCATE 挂起无输出。先设短锁等待超时，让问题显式报错而非假死 15 分钟。
        # 正确流程：跑 pytest / gate.sh 前必须 `bash scripts/dev.sh stop`。
        conn.execute(text("SET SESSION lock_wait_timeout = 10"))
        for table in ADMIN_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
    from backend.seeds.seed_admin import seed as seed_admin
    from backend.seeds.seed_configs import seed as seed_configs
    from backend.seeds.seed_dictionary import seed as seed_dictionary

    seed_admin()
    seed_configs()
    seed_dictionary()
    invalidate_config_cache()
    yield


@pytest.fixture
def db() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_headers(client: TestClient) -> dict:
    resp = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture
def staff_headers(client: TestClient) -> dict:
    resp = client.post("/api/admin/login", json={"username": "staff01", "password": "dmkwords123"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
