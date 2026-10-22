# tests/unit/test_audit9_p1_batch_20261022.py — 第九轮审查 A 批（P1 八条）
"""逐条对应 `docs/整改任务书-第九轮-20261022.md` §二 A 批：

- A2/P1-1：预约成功事件的**发布方**（第九轮实测：订阅器与微信模板映射都在，全仓零 `publish`，
  家长端永远收不到「预约成功」站内信）。
- A4/P1-3：限流桶键**只认已校验身份**（原实现给"未校验凭证"建桶 → 换伪造 token 即换桶）。
- A5/P1-4：httpx 日志脱敏（微信 AppSecret / access_token 走 URL query，httpx 在 INFO 打完整 URL）。
"""

import logging

from fastapi.testclient import TestClient

from tests.unit.test_audit8_b_batch_20261020 import _reset_limiter, _write_budget
from tests.unit.test_wm10_concurrency import _book_with_copies, _family, _h, _pay, _pay_deposit


def _db():
    from backend.database import get_session

    return get_session()


def test_reservation_created_lands_notification(client: TestClient):
    """A2/P1-1：预约成功 → 家长侧落一条「预约成功」站内信（scene + ref_id 双断言）。

    ref_id 断言不可省：通知幂等唯一键含 `ref_id`，只断 `scene` 会漏掉"键错导致第二条被吞"。
    """
    h = _h(client)
    p, c, mini = _family(client, h, "13981019001", "预约通知孩")
    _pay(client, h, c["id"], "observation_fee")
    _pay_deposit(client, h, c["id"])
    bid = _book_with_copies(client, h, "预约通知书A", 1)

    r = client.post(
        "/api/miniapp/reservations", json={"child_id": c["id"], "book_id": bid}, headers=mini
    )
    assert r.status_code == 200, r.text
    res_id = r.json()["id"]

    from backend.common.notification_models import Notification

    with _db() as db:
        rows = (
            db.query(Notification)
            .filter(
                Notification.scene == "reservation.created",
                Notification.ref_id == str(res_id),
                Notification.child_id == c["id"],
                Notification.is_deleted == 0,
            )
            .all()
        )
    assert rows, "预约成功后必须给家长落一条「预约成功」站内信（P1-1：发布方此前不存在）"
    assert rows[0].parent_id == p["id"]
    assert "预约成功" in rows[0].title


# ---------------- A4 / P1-3：限流桶键只认已校验身份 ----------------


def test_forged_token_rotation_cannot_escape_write_budget(client: TestClient):
    """A4/P1-3：**轮换伪造 token 不再增殖桶**——预算内每请求换一个伪造凭证，第 N+1 次必须 429。

    RED（旧实现：对未校验凭证取 sha1 当桶键）：N 个不同伪造 token = N 个新桶，
    每次都在自己的桶里"第一次"，**一次 429 都不会出现**（第九轮实测：轮换 3 个伪造 token
    连打 63 次 0 命中）——写预算实际无上限。
    """
    _reset_limiter()
    budget = _write_budget()
    codes = [
        client.post(
            "/api/admin/books",
            json={"isbn": "x"},  # 参数校验 422，不落库（同 audit7 全局写限流用例的造数手法）
            headers={"Authorization": f"Bearer forged-{i}"},
        ).status_code
        for i in range(budget)
    ]
    assert 429 not in codes, f"预算内不该被限流，实得 {sorted(set(codes))}"

    r = client.post(
        "/api/admin/books",
        json={"isbn": "x"},
        headers={"Authorization": "Bearer forged-one-more"},
    )
    assert r.status_code == 429, (
        f"轮换伪造 token 必须落 IP 桶并被限流，实 {r.status_code} {r.text[:120]}"
        "（RED=每个伪造凭证各自成桶）"
    )
    assert r.headers.get("Retry-After") == "60"


def test_verified_token_variants_share_one_bucket(client: TestClient, admin_headers: dict):
    """A4 反向样本：**验签通过**的身份仍是独立桶（换写法不换桶、且不吃 IP 预算）。

    判据：把 admin 的桶打满后，同一 IP 上的**伪造凭证**请求不该被 429
    （伪造落 IP 桶、admin 落 tok 桶——两者必须分开，否则真实用户被攻击者挤掉预算）。
    """
    _reset_limiter()
    budget = _write_budget()
    for _ in range(budget):
        assert (
            client.post("/api/admin/books", json={"isbn": "x"}, headers=admin_headers).status_code
            == 422
        )
    assert (
        client.post("/api/admin/books", json={"isbn": "x"}, headers=admin_headers).status_code
        == 429
    )

    r = client.post(
        "/api/admin/books", json={"isbn": "x"}, headers={"Authorization": "Bearer forged-other"}
    )
    assert r.status_code != 429, (
        f"伪造凭证落 IP 桶，不应被身份的桶牵连，实 {r.status_code}（RED=两桶被合并）"
    )


# ---------------- A5 / P1-4：httpx 日志脱敏 ----------------


def test_httpx_log_redacts_wechat_secret(caplog):
    """A5/P1-4：httpx 的 INFO 访问日志不得出现 `secret=` / `access_token=` 原文。

    RED（未挂脱敏）：httpx 在 INFO 打 `HTTP Request: GET <完整 URL>`，微信 AppSecret
    与 access_token 都在 query 里 → 能读日志即可拿到小程序主凭据。
    """
    from backend.common.log_redaction import install_log_redaction

    install_log_redaction()
    httpx_logger = logging.getLogger("httpx")
    with caplog.at_level(logging.INFO, logger="httpx"):
        httpx_logger.info(
            'HTTP Request: %s %s "%s %d %s"',
            "GET",
            "https://api.weixin.qq.com/sns/jscode2session"
            "?appid=wx123&secret=SUPERSECRETVALUE&js_code=abc&grant_type=authorization_code",
            "HTTP/1.1",
            200,
            "OK",
        )
        httpx_logger.info(
            "HTTP Request: POST https://api.weixin.qq.com/wxa/business/"
            "getuserphonenumber?access_token=ACCESSTOKENVALUE"
        )

    assert "SUPERSECRETVALUE" not in caplog.text, caplog.text
    assert "ACCESSTOKENVALUE" not in caplog.text, caplog.text
    assert "secret=[REDACTED]" in caplog.text
    assert "api.weixin.qq.com" in caplog.text, "路径/主机要保留：脱敏不是关日志"


def test_main_installs_log_redaction():
    """A5 接线断言：`backend.main` 导入即给 httpx / httpcore 挂上脱敏（防"模块写了没人调"）。"""
    import backend.main  # noqa: F401  —— 只为触发安装
    from backend.common.log_redaction import RedactSensitiveLogFilter

    for name in ("httpx", "httpcore"):
        assert any(
            isinstance(f, RedactSensitiveLogFilter) for f in logging.getLogger(name).filters
        ), f"{name} 未挂脱敏过滤器（main.py 的 install_log_redaction 掉了？）"


# ---------------- A1/A3b：新检查的「自证是活的」（红线 171 族） ----------------


def test_event_contract_check_is_live():
    """A1/P1-8：把事件契约检查改成空实现 → 架构关自证必须报失败。

    只断言"自证通过"抓不到死代码（第八轮 P1-10 的教训），必须能证伪才叫自证。
    """
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    original = mod.check_event_contract
    try:
        mod.check_event_contract = lambda errors: "mutated"
        rc = mod.self_test()
    finally:
        mod.check_event_contract = original
    assert rc == 1, "事件契约检查被改坏后架构关自证必须失败"


def test_import_cycle_check_is_live():
    """A3b：把模块级导入环检查改成空实现 → 架构关自证必须报失败。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    original = mod.check_import_cycles
    try:
        mod.check_import_cycles = lambda errors: None
        rc = mod.self_test()
    finally:
        mod.check_import_cycles = original
    assert rc == 1, "导入环检查被改坏后架构关自证必须失败"


def test_event_contract_flags_missing_publisher_in_real_repo(monkeypatch):
    """A1 真实仓回归：**摘掉一个已有发布方**，主流程必须判红（不只是夹具里能红）。"""
    from tests.unit.test_audit8_c_gate_selftest_20261020 import _load

    mod = _load("verify_architecture")
    classes, sites, published, subscribed, notes = mod._scan_event_contract(mod.BACKEND)
    assert "ReservationCreatedEvent" in published, "预约成功事件的发布方应从 A2 起存在"

    def fake_scan(backend):
        published.pop("ReservationCreatedEvent")
        return classes, sites, published, subscribed, notes

    monkeypatch.setattr(mod, "_scan_event_contract", fake_scan)
    errors: list[str] = []
    mod.check_event_contract(errors)
    assert any("reservation.created" in e for e in errors), (
        f"摘掉发布方后必须报断链/死订阅，实得 {errors}"
    )
