# tests/unit/test_audit8_e5_miniapp_configs_20261021.py — 第八轮 E 批剩余：前端口径改后端下发
"""**缺陷（第八轮审查 §3.2 前端硬编码口径副本 16 条 + P2-73）**：

小程序里 13 处硬编码业务数字（预约保留 **72 小时**、完播阈值 **95%**、观察期 **30 天**、
续借 **1 次**、活动退款 **2 小时**…）与配置中心各写一份——运营改配置后**文案不跟随**；
**P2-73** 更是三方不一致：种子与服务端是 **90 秒**、小程序倒计时写 **60 秒**，
家长 60 秒后点发送必被服务端拒（看起来像 bug）。

本批新增公开端点 `GET /api/miniapp/configs`（展示口径唯一来源），小程序全部改读它。
本文件钉住两件事：
1. **端点下发的值 = 配置中心的值**（改配置 → 下发值跟随，不是第二套常量）；
2. **小程序里不再有那 13 处硬编码**（用 AST/正则扫描源码，而不是"我记得改过了"）。
"""

from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[2]
MINIAPP = ROOT / "miniapp"

#: 下发的字段 → 配置键（与 `identity/miniapp_router.MINIAPP_CONFIG_KEYS` 同口径）
EXPECTED_FIELDS = {
    "reservation_hours": "reservation_hours",
    "audio_finish_threshold_percent": "audio_finish_threshold_percent",
    "observation_period_days": "observation_period_days",
    "transfer_review_timeout_hours": "transfer_review_timeout_hours",
    "renew_limit": "renew_limit",
    "renew_days": "renew_days",
    "sms_send_interval_seconds": "sms_send_interval_seconds",
    "quiz_questions_per_book": "quiz_questions_per_book",
    "quiz_max_attempts": "quiz_max_attempts",
    "activity_refund_cutoff_hours": "activity_refund_cutoff_hours",
}


def _h(client: TestClient) -> dict:
    r = client.post("/api/admin/login", json={"username": "admin", "password": "dmkwords123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_miniapp_configs_is_public_and_complete(client: TestClient):
    """端点**无需登录**（登录页倒计时要用）且字段齐全——少一个字段就是前端一处空白文案。

    RED（还原修复前）：端点不存在 → 404 → 本用例红。
    """
    r = client.get("/api/miniapp/configs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_FIELDS), f"字段集合不一致：{sorted(body)}"
    assert all(isinstance(v, int) and v > 0 for v in body.values()), body


def test_miniapp_configs_follows_config_center(client: TestClient):
    """**改配置 → 下发值跟随**（这是"前端口径改后端下发"的**唯一判据**）。

    做法：用超管把 `reservation_hours` 改成 48 → 端点必须返回 48；改回 72 → 又返回 72。
    若端点读的是自己的常量副本，这里必红。
    """
    h = _h(client)
    before = client.get("/api/miniapp/configs").json()["reservation_hours"]
    assert before == 72, before
    upd = client.put(
        "/api/admin/configs/reservation_hours",
        json={"value": "48", "reason": "E5 用例：口径跟随验证"},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert client.get("/api/miniapp/configs").json()["reservation_hours"] == 48
    # 还原（配置是业务数据，用例结束后必须是原值）
    back = client.put(
        "/api/admin/configs/reservation_hours",
        json={"value": "72", "reason": "E5 用例：还原"},
        headers=h,
    )
    assert back.status_code == 200, back.text
    assert client.get("/api/miniapp/configs").json()["reservation_hours"] == 72


def test_miniapp_configs_covers_sms_interval_matching_server(client: TestClient):
    """P2-73：下发的短信重发间隔必须与服务端**同一份值**（种子 90 秒，不是前端那个 60）。"""
    from backend.common.config_service import ConfigService
    from backend.database import get_session

    db = get_session()
    try:
        server_value = int(ConfigService(db).get_value("sms_send_interval_seconds"))
    finally:
        db.close()
    sent = client.get("/api/miniapp/configs").json()["sms_send_interval_seconds"]
    assert sent == server_value == 90, (sent, server_value)
    # 服务端**实际拦截**用的是这个值（行为判据，不是读常量）：61 秒前刚发过 → 拒；91 秒前 → 放行
    db = get_session()
    try:
        from datetime import datetime, timedelta

        from backend.common.exceptions import ConflictError
        from backend.domain.identity.models import SmsCode
        from backend.domain.identity.sms_service import SmsCodeService

        phone = "13900009999"
        db.add(
            SmsCode(
                phone=phone,
                code_hash="x",
                purpose="login",
                expires_at=datetime.now() + timedelta(seconds=300),
                created_at=datetime.now() - timedelta(seconds=61),
            )
        )
        db.commit()
        try:
            SmsCodeService(db).send(phone, "login")
        except ConflictError as e:
            # 61 秒 < 90 → 必须拦，且提示里应含"秒后再试"（家长据此等倒计时）
            assert "秒后再试" in str(e), e
        else:
            raise AssertionError("61 秒前刚发过却被放行——服务端间隔与下发值不同源")
    finally:
        db.close()


# ---------------------------------------------------------------- 小程序侧：硬编码必须清零


#: 被下发的口径在源码里**不该再出现**的字面量形态（用户可见文案里的数字）
FORBIDDEN_PATTERNS = (
    (r"72\s*小时", "预约保留 72 小时（改读 cfg.reservation_hours）"),
    (r"72h", "预约保留 72h（改读 cfg.reservation_hours）"),
    (r"95%", "完播阈值 95%（改读 cfg.audio_finish_threshold_percent）"),
    (r"观察期\s*30\s*天", "观察期 30 天（改读 cfg.observation_period_days）"),
    (r"限续借\s*1\s*次", "续借次数 1（改读 cfg.renew_limit）"),
)

#: 允许保留这些字样的地方（注释里的**机制说明**与兜底表；逐条写明理由）
ALLOWED_FILES = {
    "miniapp/utils/configs.js",  # 兜底表：与 seed_configs 默认值一致，仅请求失败时用
}


def test_miniapp_has_no_hardcoded_business_numbers():
    """小程序源码里不得再有被下发的业务数字（注释里的机制说明除外，逐条白名单）。

    RED（还原修复前）：13 处硬编码 → 本用例红。
    """
    offenders: list[str] = []
    for path in sorted(MINIAPP.rglob("*.js")) + sorted(MINIAPP.rglob("*.wxml")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("<!--"):
                continue  # 注释：解释机制时会引用具体数字，不算文案副本
            for pattern, why in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    offenders.append(f"{rel}:{lineno}: {why} → {stripped[:60]}")
    assert offenders == [], "小程序仍有硬编码业务数字：\n" + "\n".join(offenders)


def test_miniapp_consumers_read_configs():
    """消费点必须真的读下发值（不是"删掉硬编码改成写死别的数字"）。

    逐文件断言"引了 utils/configs 且在渲染前取口径"——只删数字不接线同样红。
    取口径有两种等价写法，都认：`await loadConfigs()`（页面等口径再渲染）与
    `loadConfigs().then(...)`（同步段先设状态、异步刷 cfg——第一百三十四批
    reservation 页的 onShow 抢跑修复）。
    """
    consumers = [
        "miniapp/pages/shelf/shelf.js",
        "miniapp/pages/reading-pkg/book-detail/book-detail.js",
        "miniapp/pages/order-pkg/reservation/reservation.js",
        "miniapp/pages/reading-pkg/quiz/quiz.js",
        "miniapp/pages/reading-pkg/reader/reader.js",
        "miniapp/pages/member/member.js",
        "miniapp/pages/order-pkg/benefit-transfer/benefit-transfer.js",
        "miniapp/pages/activity-pkg/activity-detail/activity-detail.js",
        "miniapp/pages/login/login.js",
    ]
    for rel in consumers:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "utils/configs" in text, f"{rel} 没引 utils/configs"
        assert re.search(r"(?:\bawait\s+)?\bloadConfigs\(", text), f"{rel} 没在渲染前取口径"
        assert "cfg" in text, f"{rel} 没把口径放进页面数据"


def test_miniapp_sms_countdown_uses_backend_value():
    """P2-73 正面钉：倒计时**读下发值**，不得再写死 60。

    RED（还原修复前：`this.setData({ countdown: 60 })`）：本用例红。
    """
    text = (ROOT / "miniapp/pages/login/login.js").read_text(encoding="utf-8")
    assert "countdown: this.data.cfg.sms_send_interval_seconds" in text, text[-600:]
    assert "countdown: 60" not in text, "倒计时仍写死 60（与服务端 90 不一致）"


# ---------------------------------------------------------------- 文档符号索引（本批补面）


def test_docs_symbol_index_includes_bdd_steps():
    """文档引用 BDD 步骤助手的**真实符号**不得被判悬空（符号索引必须含 `features/`）。

    来历（第一百零六批实测）：本批文档写了 `vocabulary_steps._ensure_dictionary()`，
    而 `check_docs_code_alignment` 的符号索引只扫 backend/scripts/tests → **真函数被判悬空**
    （4 处假红）。补面后必须能被查到，且不能只靠 `docs-code-ignore` 糊过去
    （豁免注释是给"刻意提及不存在的东西"用的）。

    RED（变异：把 features/ 从索引扫描面摘掉）：本用例红。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_probe_docs_code", ROOT / "scripts/check_docs_code_alignment.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    symbols = mod.collect_symbols()
    assert "_ensure_dictionary" in symbols, "BDD 步骤助手不在符号索引里（文档引用会被判悬空）"
    assert "seed" in symbols, "seeds 的 seed() 不在符号索引里"


# ---------------------------------------------------------------- 管理端侧（本批同族）

#: 管理端被下发的口径在源码里不该再出现的字面量（第八轮 §3.2 列的三处）
ADMIN_FORBIDDEN = (
    (r"锁定 72 小时", '预约锁定时长（改读 cfgNum(configs, "reservation_hours")）'),
    (r"恢复 3 次机会", '测验机会（改读 cfgNum(configs, "quiz_max_attempts")）'),
    (r"测验未满 5 道", '测验题量（改读 cfgNum(configs, "quiz_questions_per_book")）'),
    (r"取前 \$\{Math\.min\(5,", '正式题量（改读 cfgNum(configs, "quiz_questions_per_book")）'),
)


def test_admin_web_has_no_hardcoded_business_numbers():
    """管理端源码里不得再有那三处硬编码口径（注释除外）。

    RED（还原修复前）：`Reservations.tsx` 的「副本锁定 72 小时」、`GrowthManage.tsx` 的
    「恢复 3 次机会」、`BookManage.tsx`/`BookDetail.tsx` 的「5 道」→ 本用例红。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "admin-web/src").rglob("*.tsx")):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for pattern, why in ADMIN_FORBIDDEN:
                if re.search(pattern, line):
                    offenders.append(f"{rel}:{lineno}: {why} → {stripped[:70]}")
    assert offenders == [], "管理端仍有硬编码口径：\n" + "\n".join(offenders)


def test_admin_web_consumers_read_configs():
    """四处管理端消费点必须真的读配置（引 `useConfigs` + 用 `cfgNum`）。"""
    for rel in (
        "admin-web/src/pages/Reservations.tsx",
        "admin-web/src/pages/GrowthManage.tsx",
        "admin-web/src/pages/BookManage.tsx",
        "admin-web/src/pages/BookDetail.tsx",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "useConfigs" in text, f"{rel} 没引 useConfigs"
        assert "cfgNum(configs," in text, f"{rel} 没读配置值（cfgNum）"
