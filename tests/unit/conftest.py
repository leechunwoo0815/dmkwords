# tests/unit/conftest.py — WM1 测试夹具（真实 MySQL + 真实 HTTP 层）
"""反假绿纪律：测试走真实 MySQL（TRUNCATE 隔离）+ TestClient（真实 ASGI/HTTP 栈）。"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

# G-12/T34（域G）：测试上传隔离到临时目录—— uploads 与 dev 库同源共写
# （pytest 与 dev 后端共库，TRUNCATE 清业务表；上传文件此前 311MB/4136 个
# 孤儿文件永久残留）。须在首次 import backend 前设置环境变量。
os.environ.setdefault("UPLOADS_DIR", tempfile.mkdtemp(prefix="dmk-test-uploads-"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import SessionLocal, engine
from backend.domain.admin.service import invalidate_config_cache
from backend.main import app

ADMIN_TABLES = [
    "admin_notifications",
    "notifications",
    "task_run_logs",
    "dead_letters",
    # 媒体体检（2026-09-23）：盘点报告与回收站条目——每测试清空，否则"最新一份报告"
    # 会跨测试串台（下个用例的盘点断言读到上个用例的行）
    "media_trash_entries",
    "media_censuses",
    # 短信验证码（2026-10-08 登录接线）：同号 60 秒发送间隔是**按库里的最新一条**判的，
    # 不清就会跨测试互相限流（实测：连跑两轮，第二轮全红）
    "sms_codes",
    "observation_reports",
    "transfer_requests",
    "withdrawal_requests",
    "refund_requests",
    # P2 #28（2026-10-16，第三轮大审查）：对账表原先漏清——LEDGER 的"第四十九批·清理补对账表"
    # 与代码不符（grep 该表名 = 0）。漏清的后果：对账类用例读到上个用例残留的账单行。
    "payment_reconciliations",
    "activity_enrollments",
    "activities",
    "vocabularies",
    "favorites",
    "circle_likes",
    "circle_posts",
    "circle_rank_snapshots",
    # dictionary_words 不清（2026-08-29 用户裁定；2026-09-02 更新：开发期词库
    # 已裁至 100 行+seed 演示词 ≈309 行，全量 335 万备份在
    # ~/dmkwords-backups/dictionary_words-full-20260902.sql.gz，上线再导回；
    # gate 禁全表读写词库——超时根因）；seed_dictionary 幂等补演示词，lookup 测试自洽
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


def _force_books_on_shelf() -> None:
    """把当前库里所有书目置为上架态（仅测试环境；见 clean_db 注释）。"""
    with engine.begin() as conn:
        conn.execute(text("UPDATE books SET status = 1 WHERE is_deleted = 0"))


#: 这些模块考的就是"新建书目默认下架 + 上架完整性五检"——不能被"建书即上架"基建覆盖
_OFF_SHELF_CONTRACT_MODULES = (
    "test_wm2_catalog",
    "test_wm2_d1d3",
    "test_wm2_p2",
    # 2026-10-18 补：软删同号**复活**后同样必须回下架态（媒体已在软删时清除）
    "test_audit5_p1_batch1_20261018",
)


@pytest.fixture(autouse=True)
def books_on_shelf_by_default(monkeypatch, request):
    """测试里**新建的书默认上架**（2026-10-12，P6 下架禁借落地后的测试基建口径）。

    为什么不在建后统一 UPDATE：书是在用例执行**过程中**建的，建完立刻就要借。
    这里包住 BookService.create——建完把 status 置为上架（需要测下架行为的用例自行 toggle）。
    """
    from backend.domain.catalog.models import Book
    from backend.domain.catalog.service import BookService

    orig = BookService.create_book

    def _create_book(self, admin, payload):  # noqa: ANN001
        book = orig(self, admin, payload)
        book.status = Book.STATUS_ON
        self.db.commit()
        return book

    module = getattr(request.node, "module", None)
    if module is not None and module.__name__.split(".")[-1] in _OFF_SHELF_CONTRACT_MODULES:
        yield  # D1 契约用例：保持"新建默认下架"，不改行为
        return
    monkeypatch.setattr(BookService, "create_book", _create_book)
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试独立数据：截断 admin 三表 → 重播种子 → 清配置缓存。"""
    # I-3/T8：RateLimiter 全局实例（内存）跨测试累积会误伤——每测试重置
    from backend.middleware.rate_limit import _limiter as _rate_limiter

    _rate_limiter._requests.clear()
    # 第八轮审查 P1-3：支付异常留痕的分钟预算同样是进程内状态，跨测试累积会误伤
    from backend.domain.identity.payment_notify import reset_anomaly_budget

    reset_anomaly_budget()
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
    # 2026-10-12（P6 下架禁借落地后）：测试里**新建的书默认上架**——否则所有"建书即借"的
    # 历史用例都会撞上"已下架，不能借出"。需要测下架行为的用例自行 toggle 即可。
    _force_books_on_shelf()
    yield


@pytest.fixture
def db() -> Generator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session_pair() -> Generator:
    """P1 并发测试基建：双独立 session（各起事务）。

    用法：session A 手动锁定主体行（with_for_update 不提交）模拟先到者，
    session B 走被测 service —— B 应阻塞后读到 A 已提交的新状态并失败/跳过，
    而非快照覆盖写。注意 MySQL REPEATABLE READ：B 会话跨事务读 A 提交的数据
    需先 commit/expire_all 刷新快照（先例注释 test_wm13_admin_notify.py）。
    """
    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        yield s1, s2
    finally:
        s1.rollback()
        s2.rollback()
        s1.close()
        s2.close()


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
