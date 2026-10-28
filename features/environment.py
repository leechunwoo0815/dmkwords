# features/environment.py — behave 测试环境（真实 MySQL + 真实 HTTP 层）
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import engine
from backend.domain.admin.service import invalidate_config_cache
from backend.main import app
from tests.unit.conftest import ADMIN_TABLES

# G-13/T34（域G）：清理清单对齐 conftest 单一事实源（原 13 表清单缺 refund/
# withdrawal/transfer/activities/notifications 等 23 表——横切批解封资金类
# feature 时会踩跨场景残留；改清单须改 conftest 一处）。
# W8-D9（2026-10-28）：原 `ADMIN_TABLES + BDD_TABLES` 每场景重复 37 条 TRUNCATE
# （BDD_TABLES 是 ADMIN_TABLES 去掉三张种子表的子集——少清那三张会破坏种子，故必须清全量）。


def before_scenario(context, scenario):
    if "draft" in scenario.effective_tags:
        return
    # I-3/T8 同族：RateLimiter 全局实例（内存）在 behave 进程内跨场景累积，
    # admin login 第 6 次即 429（gate p0batch1 首轮 BDD 11 场景失败实证）——
    # 对齐 tests/unit/conftest.py clean_db 的重置
    from backend.middleware.rate_limit import _limiter as _rate_limiter

    _rate_limiter._requests.clear()
    with engine.begin() as conn:
        # W8-D9（2026-10-28）：BDD_TABLES 是 ADMIN_TABLES 的子集（37 ⊂ 40），
        # 原先 `ADMIN_TABLES + BDD_TABLES` 每场景多跑 37 条重复 TRUNCATE。
        for table in ADMIN_TABLES:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
    from backend.seeds.seed_admin import seed as seed_admin
    from backend.seeds.seed_configs import seed as seed_configs
    from backend.seeds.seed_dictionary import seed as seed_dictionary

    seed_admin()
    seed_configs()
    # 第八轮审查 P2-68（2026-10-21 复核补）：**BDD 环境与 conftest 同口径**——原先只播
    # admin/config 两支，词典靠 `vocabulary_steps` 的用点自愈（`_ensure_dictionary()`）兜住。
    # 复核结论：报告所述"BDD 单独运行会失败"**未复现**（生词本 feature 的背景步会播词典，
    # 且全仓只有它依赖词典内容）；但环境级差异是真的——将来新场景直接查词而不走那个背景步
    # 就会踩到。这里补一行消除陷阱：词典表**不在每场景清表清单内**（conftest 有意不清），
    # 所以 seed 幂等且只有 1 次 `IN` 查询/场景（表内 ~310 行，非 340 万全量）。
    seed_dictionary()
    invalidate_config_cache()
    context.client = TestClient(app)
    context.admin_token = None
    context.staff_token = None


def _login(context, username: str, password: str) -> str:
    resp = context.client.post(
        "/api/admin/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["token"]


def get_admin_headers(context) -> dict:
    if not context.admin_token:
        context.admin_token = _login(context, "admin", "dmkwords123")
    return {"Authorization": f"Bearer {context.admin_token}"}


def get_staff_headers(context) -> dict:
    if not context.staff_token:
        context.staff_token = _login(context, "staff01", "dmkwords123")
    return {"Authorization": f"Bearer {context.staff_token}"}
