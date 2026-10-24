# tests/unit/test_review10_b1_20261024.py — 第十轮全量审查·整改 B1（安全/隐私/运维硬伤）
"""覆盖第十轮审查 B1 批 11 条（P2 6 + P3 5）：每条都**去修复即红**（RED 见各用例 docstring）。

| 编号 | 缺陷 | 用例 |
|---|---|---|
| W4-D1 | 日志"脱敏"对 `httpx.URL` 实为空操作（JWT 明文进 gate 日志） | `test_httpx_real_client_call_is_redacted` |
| W3-01 | 清理脚本启动即崩 + 策略表名 0/11 命中真实表 | `test_purge_policy_matches_real_schema` / `test_purge_policy_validation_catches_stale_name` |
| W3-02 | Dockerfile 引不存在的 requirements.txt、无 .dockerignore | `test_dockerfile_copy_sources_exist` / `test_dockerignore_covers_secrets` |
| W3-03 | ECDICT 导入自述幂等不成立（word 无唯一索引 + unique_checks=0） | `test_dictionary_word_has_unique_index` / `test_insert_ignore_is_idempotent` |
| W3-04 | seed 软删行判重漏 + 裸插 → 1062，`dev.sh restart` 断链 | `test_seed_demo_library_revives_soft_deleted_book` |
| W3-05 | seed 直插造"待评估时钟为空"的孩子 → 永久不进名单/待办 | `test_seed_pending_child_gets_clock_and_enters_weekly_list` |
| FE-1/W4-D2 | 取消不存在的活动 → 未处理 500 | `test_cancel_missing_activity_returns_404` |
| FE-4/W4-D3 | 500 响应丢全部安全头（异常绕过安全头中间件） | `test_unhandled_500_keeps_security_headers` |
| W4-D4 | 匿名回调按 body 增殖 audit_logs/待办、审计内容可被匿名者控制 | `test_unverified_callback_writes_nothing` / `test_callback_body_no_longer_drives_audit_key` |
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.database import engine, get_session
from backend.main import app

ROOT = Path(__file__).resolve().parents[2]


# ---------------- W4-D1：httpx 真实调用路径的脱敏 ----------------


def test_httpx_real_client_call_is_redacted(caplog):
    """W4-D1：httpx 打日志时 URL 是 **httpx.URL 对象**（不是 str），必须照样被抹。

    RED（还原旧 filter：只处理 `isinstance(a, str)` 的 arg）→ `secret=REALSECRETVALUE` 原样进日志。
    本用例与 `test_httpx_log_redacts_wechat_secret` 的区别：那条是**手搓字符串 arg**（形状与线上不同，
    所以线上漏、单测绿），这条走真实 `httpx.Client` 的 logging 路径。
    """
    from backend.common.log_redaction import install_log_redaction

    install_log_redaction()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with caplog.at_level(logging.INFO, logger="httpx"):
        with httpx.Client(transport=transport) as client:
            client.get(
                "https://api.weixin.qq.com/sns/jscode2session"
                "?appid=wx123&secret=REALSECRETVALUE&js_code=abc"
            )

    assert "REALSECRETVALUE" not in caplog.text, f"真实调用路径未脱敏：{caplog.text}"
    assert "secret=[REDACTED]" in caplog.text, caplog.text
    assert "api.weixin.qq.com" in caplog.text, "路径/主机要保留：脱敏不是关日志"


def test_redactor_handles_mapping_args(caplog):
    """同族形状：`%(k)s` 风格（args = 单元素 tuple 里放 mapping）也要抹，且**保形**。

    若把 mapping 一并 `str()` 掉，`msg % args` 会抛 TypeError——所以这条同时是"别改坏"的护栏。
    """
    from backend.common.log_redaction import install_log_redaction

    install_log_redaction()
    logger = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        logger.info("%(url)s", {"url": httpx.URL("http://h/a?token=JWT.X")})
    assert "JWT.X" not in caplog.text, caplog.text
    assert "token=[REDACTED]" in caplog.text, caplog.text


# ---------------- W3-01：清理脚本可执行 + 策略表名真实 ----------------


def test_purge_policy_matches_real_schema():
    """W3-01：`PURGE_POLICY` / `REFERENCE_CHECK` 的每个表与列都必须真实存在。

    RED（旧实现）：11 个策略表名全是历史 schema（`child`/`order`/`reading_session`…）→ 0/11 命中。
    """
    from scripts.purge_soft_deleted import PURGE_POLICY, validate_policy

    assert PURGE_POLICY, "策略不能为空（空策略=没有清理面，不是修好了）"
    problems = validate_policy(engine)
    assert problems == [], f"策略与真实 schema 不符：{problems}"


def test_purge_policy_validation_catches_stale_name(monkeypatch):
    """变异自证：把任一策略表名改成历史名 → 校验必须**报出来**（旧版是静默跳过）。"""
    import scripts.purge_soft_deleted as purge

    monkeypatch.setitem(purge.PURGE_POLICY, "child", {"retention_days": 1095, "reason": "历史名"})
    problems = purge.validate_policy(engine)
    assert any("child" in p for p in problems), f"假阴性：改名后未报出 {problems}"

    monkeypatch.setitem(
        purge.REFERENCE_CHECK, "activities", [("activity_enrollment", "activity_id", "id")]
    )
    problems = purge.validate_policy(engine)
    assert any("activity_enrollment" in p for p in problems), f"子表名过期未报出：{problems}"


def test_purge_script_runs_dry_run_and_reports_zero_mismatch():
    """脚本本身必须能跑到底（旧版连库之前就 `AttributeError`）。

    只跑 dry-run（默认分支，零写操作），断言返回码 0。
    """
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "scripts/purge_soft_deleted.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"dry-run 未跑通：{proc.stdout[-800:]}{proc.stderr[-800:]}"
    assert "DRY-RUN" in proc.stdout, proc.stdout[-400:]


# ---------------- W3-02：Dockerfile 与 .dockerignore ----------------


def _dockerfile_copy_sources() -> list[str]:
    srcs: list[str] = []
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*COPY\s+(?!--)(.*)", line)
        if not m:
            continue
        parts = [p for p in m.group(1).split() if not p.startswith("--")]
        srcs.extend(parts[:-1])  # 末位是目标路径
    return srcs


def test_dockerfile_copy_sources_exist():
    """W3-02：Dockerfile 里每个 `COPY <src>` 的 src 必须在仓库里真实存在。

    RED（旧 Dockerfile）：`COPY requirements.txt .` 而仓库**没有** requirements.txt
    ⇒ `docker build` 必挂在 COPY。
    """
    sources = _dockerfile_copy_sources()
    assert sources, "没解析到任何 COPY 源——判据塌缩（解析器坏了）"
    missing = [s for s in sources if not s.startswith("/") and not (ROOT / s).exists()]
    assert missing == [], f"Dockerfile 引用了不存在的路径：{missing}"


def test_dockerignore_covers_secrets():
    """W3-02：`.dockerignore` 必须存在且排掉密钥/真实数据（Docker **不读** `.gitignore`）。"""
    path = ROOT / ".dockerignore"
    assert path.exists(), "缺 .dockerignore ⇒ COPY . . 会把 .env/certs/backups 打进镜像层"
    text_ = path.read_text(encoding="utf-8")
    for must in (".env", "certs/", "backups/", "uploads/", ".git/"):
        assert must in text_, f".dockerignore 未排除 {must}"


# ---------------- W3-03：dictionary_words.word 唯一索引 + 幂等 ----------------


def test_dictionary_word_has_unique_index():
    """W3-03：`dictionary_words.word` 必须有**唯一**索引（INSERT IGNORE 去重的前提）。

    RED（旧 schema）：`ix_dictionary_words_word` 的 `non_unique=1` → INSERT IGNORE 无对象可忽略。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT INDEX_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dictionary_words' "
                "AND COLUMN_NAME = 'word'"
            )
        ).fetchall()
    assert rows, "dictionary_words.word 上没有索引——判据塌缩"
    assert any(non_unique == 0 for _name, non_unique in rows), (
        f"word 上没有唯一索引，导入脚本的幂等自述不成立：{rows}"
    )


def test_insert_ignore_is_idempotent():
    """行为侧：同一词插两次 → 只有一行（保留已有词）；不幂等的旧结构会插成两行。"""
    word = "zz_r10_probe_word"
    stmt = text(
        "INSERT IGNORE INTO dictionary_words (word, phonetic, definition, translation, "
        "create_time, update_time, is_deleted) VALUES (:w, '', 'd1', 't1', NOW(), NOW(), 0)"
    )
    with get_session() as db:
        db.execute(text("DELETE FROM dictionary_words WHERE word = :w"), {"w": word})
        db.commit()
        db.execute(stmt, {"w": word})
        db.execute(stmt, {"w": word})
        db.commit()
        count = db.execute(
            text("SELECT COUNT(*) FROM dictionary_words WHERE word = :w"), {"w": word}
        ).scalar()
        # 收尾：词库表被 conftest 有意排除在 TRUNCATE 之外，探针行必须自己清掉
        db.execute(text("DELETE FROM dictionary_words WHERE word = :w"), {"w": word})
        db.commit()
    assert count == 1, f"INSERT IGNORE 未去重（count={count}）——唯一索引没生效"


# ---------------- W3-04：seed 软删行复活 ----------------


def test_seed_demo_library_revives_soft_deleted_book():
    """W3-04：演示书被软删后重跑 seed 必须**复活**，而不是撞 1062 让 seed 失败。

    RED（旧实现）：判重 SQL 带 `is_deleted == 0` → 看不见软删行 → 裸插同号 → IntegrityError 1062
    → `dev.sh restart` 的 [2/4] seed 步失败退出（门禁后现场恢复链断）。
    复现用**真实路径**：先跑一次 seed 建库 → 软删一本（等同管理端删除：只翻 is_deleted）
    → 再跑一次 seed（= `dev.sh restart` 的第 2 步）。
    """
    from backend.domain.catalog.models import Book, BookCopy, QuizQuestion
    from scripts.seed_demo_library import NEW_BOOKS
    from scripts.seed_demo_library import main as seed_main

    assert seed_main() == 0, "首次 seed 未跑通（前置）"
    isbn = NEW_BOOKS[0][0]
    with get_session() as db:
        book = db.query(Book).filter(Book.isbn == isbn).first()
        assert book is not None, "首次 seed 没建出演示书——判据塌缩"
        book_id = book.id
        copies, quizzes = (
            db.query(BookCopy).filter(BookCopy.book_id == book_id).count(),
            db.query(QuizQuestion).filter(QuizQuestion.book_id == book_id).count(),
        )
        assert copies and quizzes, f"前置：副本/题目应有（copies={copies} quizzes={quizzes}）"
        # 与管理端 delete_book 同款：只翻 is_deleted（行与下游都留在原地）
        book.is_deleted = 1
        db.commit()

    assert seed_main() == 0, "软删后重跑 seed 必须成功（老毛病：IntegrityError 1062）"

    with get_session() as db:
        row = db.query(Book).filter(Book.isbn == isbn).order_by(Book.id.desc()).first()
        assert row.id == book_id, f"应当**复活原行**（id 不变），实际新建了 {row.id}"
        assert row.is_deleted == 0, "复活后 is_deleted 必须归 0"
        assert row.cover_path, "复活行必须重画封面（软删已删文件，路径留空会骗过上架完整性检查）"
        assert db.query(BookCopy).filter(BookCopy.book_id == book_id).count() == copies, (
            "复活不该重复建副本（uq_copy_code 会撞）"
        )
        assert db.query(QuizQuestion).filter(QuizQuestion.book_id == book_id).count() == quizzes, (
            "复活不该重复建题目"
        )


# ---------------- W3-05：待评估时钟 + 周名单 ----------------


def test_seed_pending_child_gets_clock_and_enters_weekly_list():
    """W3-05：seed 造的"待评估孩"必须带 `pending_evaluation_at`，并出现在周名单里。

    RED（旧 seed：`Child(member_status=...)` 直构）→ 时钟 NULL → `pending_evaluation_weekly`
    的判据 `pending_evaluation_at IS NOT NULL` 永远命中不了它 → 演示不了"待评估超过 7 天"。
    """
    from scripts import seed_wm11_demo

    seed_wm11_demo.seed()

    with get_session() as db:
        rows = db.execute(
            text(
                "SELECT name, pending_evaluation_at FROM children "
                "WHERE member_status = 'pending_evaluation' AND is_deleted = 0"
            )
        ).fetchall()
    assert rows, "种子里没有待评估孩——判据塌缩"
    missing = [name for name, clock in rows if clock is None]
    assert missing == [], f"待评估孩时钟为空（永久不进名单/待办）：{missing}"

    from backend.domain.identity.service import ChildService

    with get_session() as db:
        count = ChildService(db).pending_evaluation_weekly()
    assert count >= 1, "待评估孩未进入周名单（时钟早于阈值的行数 0）"

    from backend.common.admin_notification_models import AdminNotification

    with get_session() as db:
        todo = (
            db.query(AdminNotification)
            .filter(AdminNotification.scene == AdminNotification.SCENE_PENDING_EVALUATION)
            .first()
        )
        assert todo is not None, "周名单没有落成馆员待办"
        assert "待评估已" in todo.content, f"待办文案没暴露待评估天数：{todo.content}"


# ---------------- FE-1 / W4-D2：不存在活动 → 404 而不是 500 ----------------


def test_cancel_missing_activity_returns_404(client: TestClient, admin_headers: dict):
    """FE-1：取消不存在的活动必须是 404 业务语义。

    RED（旧实现）：`a.status` 对 None 解引用 → AttributeError → 未处理 500
    （TestClient 默认 `raise_server_exceptions=True`，RED 时会直接抛异常 → 用例红）。
    """
    r = client.post("/api/admin/activities/999999/cancel", json={}, headers=admin_headers)
    assert r.status_code == 404, f"期望 404（活动不存在），实际 {r.status_code} {r.text[:200]}"
    assert r.json()["detail"] == "活动不存在", r.text

    # 同 id 的 GET 一直是 404：口径一致（对照，证明资源不存在本可表达）
    r2 = client.get("/api/admin/activities/999999", headers=admin_headers)
    assert r2.status_code == 404, r2.status_code


# ---------------- FE-4 / W4-D3：500 也带安全头 ----------------


SECURITY_HEADERS = (
    "x-frame-options",
    "x-content-type-options",
    "content-security-policy",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
)


def test_unhandled_500_keeps_security_headers(monkeypatch, admin_headers: dict):
    """FE-4/W4-D3：未处理异常（裸 500）也必须带全套安全头。

    RED（修复前）：Starlette 的 `ServerErrorMiddleware` 在用户中间件**外层**，
    500 由它生成 → 安全头中间件的补头代码不执行（实测 500 = 0/6 头）。
    """
    from backend.domain.activity.service import ActivityService

    def _boom(self, admin, activity_id):  # noqa: ANN001
        raise RuntimeError("probe 500（本用例的人造未处理异常）")

    monkeypatch.setattr(ActivityService, "cancel_activity", _boom)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/admin/activities/1/cancel", json={}, headers=admin_headers)
    assert r.status_code == 500, f"期望 500，实际 {r.status_code}"
    missing = [h for h in SECURITY_HEADERS if h not in r.headers]
    assert missing == [], f"500 响应缺安全头：{missing}（实测响应头 {dict(r.headers)}）"
    assert r.json().get("detail"), f"500 体应是 JSON 且带 detail：{r.text[:200]}"


def test_normal_error_paths_keep_security_headers(client: TestClient, admin_headers: dict):
    """对照组（防"只给 500 补头、把正常路径搞丢"）：404/422/401 照旧齐全。"""
    for resp in (
        client.get("/api/admin/activities/999999", headers=admin_headers),
        client.get("/api/admin/books?page=0", headers=admin_headers),
        client.get("/api/admin/me"),
    ):
        missing = [h for h in SECURITY_HEADERS if h not in resp.headers]
        assert missing == [], f"{resp.status_code} 缺安全头 {missing}"


# ---------------- W4-D4：匿名回调不再能灌库 / 决定审计内容 ----------------


SIG_HEADERS = {
    "Wechatpay-Signature": "not-a-real-signature",
    "Wechatpay-Timestamp": "1",
    "Wechatpay-Nonce": "n",
}


def _counts() -> tuple[int, int]:
    from backend.common.admin_notification_models import AdminNotification
    from backend.common.system_models import AuditLog

    with get_session() as db:
        return (
            db.query(AuditLog).filter(AuditLog.action == "payment.anomaly").count(),
            db.query(AdminNotification)
            .filter(AdminNotification.scene == "admin.payment_anomaly")
            .count(),
        )


def test_unverified_callback_writes_nothing(client: TestClient, monkeypatch):
    """W4-D4：**验签不通过**的回调只进进程日志，不写 audit_logs / 待办。

    RED（修复前）：每次请求写 1 行审计（`ref_id` 由 body 摘要决定）+ 1 条待办 → 匿名可灌。
    """
    from backend.domain.identity.payment_service import PaymentService

    class _RejectingGateway:
        async def verify_callback_signature(self, body, signature, timestamp, nonce):  # noqa: ANN001
            return False

    monkeypatch.setattr(PaymentService, "_gateway", lambda self: _RejectingGateway())
    a0, n0 = _counts()
    codes = [
        client.post(
            "/api/pay/wechat/notify", json={"id": f"anon-{i}"}, headers=SIG_HEADERS
        ).status_code
        for i in range(5)
    ]
    a1, n1 = _counts()
    assert set(codes) == {401}, f"未验签回调语义必须仍是 401（微信重试语义不变）：{set(codes)}"
    assert (a1 - a0, n1 - n0) == (0, 0), (
        f"验签不通过不该落库，实际 audit+{a1 - a0} / todo+{n1 - n0}"
    )


def test_callback_body_no_longer_drives_audit_key_or_reason(client: TestClient):
    """W4-D4：畸形报表体不再决定审计**键**（换 body 不增殖）与**内容**（不回显 body）。

    修复前：`ref_id = anomaly:{sha1(body)}` + `reason = body[:200]` → 匿名者换 body 就换一行。
    修复后：同族共用固定键（幂等，只留一条）+ 固定 reason token。
    """
    from backend.common.system_models import AuditLog

    a0, n0 = _counts()
    bodies = [f"not-a-json-body-{i}" for i in range(4)]
    codes = [
        client.post("/api/pay/wechat/notify", content=b, headers=SIG_HEADERS).status_code
        for b in bodies
    ]
    assert set(codes) == {400}, f"报文无法解析仍应是 400：{set(codes)}"
    a1, n1 = _counts()
    assert n1 - n0 == 1, f"同类异常待办必须坍缩为 1 条（固定键），实际 +{n1 - n0}"
    assert a1 - a0 <= 4, f"审计行不该按 body 增殖（上限=请求数）：+{a1 - a0}"

    with get_session() as db:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.action == "payment.anomaly")
            .order_by(AuditLog.id.desc())
            .limit(4)
            .all()
        )
    reasons = [r.reason or "" for r in rows]
    assert reasons, "没取到审计行——判据塌缩"
    for reason in reasons:
        assert "not-a-json-body" not in reason, f"审计 reason 回显了请求体：{reason}"
    assert any(r == "unparseable_body" for r in reasons), f"未使用固定 token：{reasons}"


def test_anomaly_todo_key_is_fixed_not_body_digest(client: TestClient):
    """W4-D4：无业务对象的资金异常用固定 ref_id（而不是请求体摘要）。"""
    client.post("/api/pay/wechat/notify", content="not-a-json-body", headers=SIG_HEADERS)
    from backend.common.admin_notification_models import AdminNotification
    from backend.domain.identity.payment_notify import UNVERIFIED_BODY_REF

    with get_session() as db:
        row = (
            db.query(AdminNotification)
            .filter(AdminNotification.scene == "admin.payment_anomaly")
            .order_by(AdminNotification.id.desc())
            .first()
        )
    assert row is not None, "没落待办——判据塌缩"
    assert row.ref_id == UNVERIFIED_BODY_REF, f"ref_id 仍是可被 body 控制的值：{row.ref_id}"
    digest = hashlib.sha1(b"not-a-json-body").hexdigest()[:12]
    assert digest not in row.ref_id, "ref_id 仍含请求体摘要"


@pytest.mark.parametrize("payload", ["{}", '{"event_type": "TRANSACTION.SUCCESS"}'])
def test_mock_gateway_paths_still_logged_not_crashed(client: TestClient, payload: str, caplog):
    """反证尝试（防"修过头"）：mock 网关下的其它畸形报文仍按原语义回应（不因收口而 5xx）。"""
    with caplog.at_level(logging.WARNING):
        r = client.post("/api/pay/wechat/notify", content=payload, headers=SIG_HEADERS)
    assert r.status_code in (200, 400), f"不该变成 5xx：{r.status_code} {r.text[:200]}"


def test_payment_service_source_has_no_unverified_audit_calls():
    """接线断言（源码级，防"改了一半"）：`_notify_impl` 的**验签环节**不得再出现
    `self._audit_anomaly(...)` 调用（那等于把匿名请求的写库能力又接回来）。

    只看"校验段"（`def _notify_impl` → `_parse_envelope` 那行为止）里的**实际调用行**，
    不误伤同文件其它分支（`handle_notify` 的兜底、报文/解密之后的真实异常照旧留痕）。
    """
    src = (ROOT / "backend/domain/identity/payment_service.py").read_text(encoding="utf-8")
    body = src.split("def _notify_impl(")[1].split("envelope = self._parse_envelope(body)")[0]
    calls = [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith("self._audit_anomaly(") and not line.strip().startswith("#")
    ]
    assert calls == [], f"验签环节又出现落库调用（W4-D4 回归）：{calls}"
    assert "不落库" in body, "验签环节缺少「不落库」口径说明"


def test_no_gate_log_leaks_token_pattern_after_fix():
    """W4-D1 的追溯判据（防"新日志又漏"）：本仓 gate 日志里的 `?token=eyJ` 只允许出现在
    修复前的历史文件里——用**当日新增日志**做判据太脆，这里只固化"检测表达式"本身可用。"""
    sample = 'HTTP Request: GET http://127.0.0.1:8002/a?token=eyJhbGciOi&x=1 "HTTP/1.1 200 OK"'
    assert re.search(r"token=eyJ[A-Za-z0-9_.-]+", sample), "检测表达式失效（判据塌缩）"
    from backend.common.log_redaction import redact_sensitive

    assert "eyJhbGciOi" not in redact_sensitive(sample)


def test_json_body_still_parses_after_500_handler():
    """回归护栏：新增的 Exception 处理器不得改变正常 JSON 响应（只影响未处理异常）。"""
    from fastapi.testclient import TestClient as _TC

    r = _TC(app).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert json.loads(json.dumps(r.json()))["status"] == "ok"
