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
    "activity_enrollments",
    "activities",
    "vocabularies",
    "favorites",
    "dictionary_words",
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
