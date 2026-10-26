# tests/unit/test_review11_r11a_20261026.py — 第十一轮整改 R11-a（第一百四十九批）
"""R11-a 五条的机器判据（第十一轮审查报告 `docs/审查报告-第十一轮-20261025.md` §二）：

- **R11-01**：镜像钉 `TZ=Asia/Shanghai` 且显式装 `tzdata`（容器默认 UTC ⇒ 业务日边界偏 8h）。
- **R11-06**：`.dockerignore` 排掉 812MB 词库 `data/` 与内部报告 `外部专家意见/`。
- **R11-02**：证书轮换落盘后必须清 `get_payment_gateway()` 的 lru_cache（否则回调继续用旧证书验签）。
- **R11-03**：任务失败必须收口**同一条** `TaskRunLog`（任务体内层 commit 会让 RUNNING 行落库，
  再插一条 failed ⇒ 看板"连续失败"恒为 1）。

RED（可证伪）：
- 删 Dockerfile 的 `TZ=Asia/Shanghai` / `tzdata` → 前两条红；
- 删 `_persist_platform_cert` 里的 `reset_payment_gateway_cache()` → 第三条红（currsize 仍为 1）；
- 把 `finalize_failed_run` 改回"新插一条 failed" → 第四条红（2 行：running + failed）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from backend.common.notification_models import TaskRunLog
from backend.database import get_session

ROOT = Path(__file__).resolve().parent.parent.parent


def _directive_lines(text_: str) -> list[str]:
    """只取非注释行（判据要认**指令**，不认注释里的"提及"）。"""
    return [
        line.strip()
        for line in text_.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_dockerfile_pins_timezone():
    """R11-01：镜像必须钉 TZ 并装 tzdata（缺 zoneinfo 时 TZ 会被静默忽略 → 仍走 UTC）。"""
    lines = _directive_lines((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert any("TZ=Asia/Shanghai" in line for line in lines), (
        "镜像未钉 TZ（注释里写了不算）：容器默认 UTC ⇒ 业务日边界整体偏 8 小时"
    )
    assert any("tzdata" in line for line in lines), (
        "未显式安装 tzdata：slim 镜像缺 /usr/share/zoneinfo 时 TZ 会被忽略"
    )


def test_dockerignore_excludes_bulk_data_and_internal_reports():
    """R11-06：构建上下文不得包含 812MB 词库与内部报告目录。"""
    lines = _directive_lines((ROOT / ".dockerignore").read_text(encoding="utf-8"))
    for must in ("data/", "外部专家意见/"):
        assert must in lines, f".dockerignore 未排除 {must}（会被 COPY . . 打进构建上下文）"


def test_cert_refresh_resets_gateway_cache(monkeypatch, tmp_path):
    """R11-02：平台证书落盘后必须清网关单例缓存。"""
    from backend.integrations import payment
    from backend.integrations.wechat import pay_v3

    payment.reset_payment_gateway_cache()
    monkeypatch.setattr(payment, "get_settings", lambda: SimpleNamespace(PAYMENT_PROVIDER="mock"))
    assert payment.get_payment_gateway() is not None
    assert payment.get_payment_gateway.cache_info().currsize == 1

    cert_file = tmp_path / "cert.pem"
    monkeypatch.setattr(
        pay_v3,
        "get_settings",
        lambda: SimpleNamespace(WECHAT_PLATFORM_CERT_PATH=str(cert_file)),
    )
    gateway = object.__new__(pay_v3.WeChatPayV3)
    gateway.platform_cert = None
    gateway._persist_platform_cert(b"NEW-CERT", object())

    assert cert_file.read_bytes() == b"NEW-CERT"
    assert payment.get_payment_gateway.cache_info().currsize == 0, (
        "证书轮换后未清网关缓存：回调会继续用旧证书验签（换证后全部 401：钱收了、单不认）"
    )
    payment.reset_payment_gateway_cache()


def test_failed_task_run_reuses_running_row():
    """R11-03：内层已提交 RUNNING 行时，失败必须就地收口，不能追加第二条 failed。"""
    from backend.tasks.registry import TaskSpec, _run_task_locked

    name = "zz_r11_tasklog_probe"

    def boom(db):
        db.execute(text("SELECT 1"))
        db.commit()  # 模拟 backend/common/batch.py：任务体内层提交 ⇒ RUNNING 行落库
        raise RuntimeError("probe boom")

    spec = TaskSpec(name, "R11-03 探针", "系统", 60, boom)
    with get_session() as db:
        db.query(TaskRunLog).filter(TaskRunLog.task_name == name).delete()
        db.commit()
    try:
        out = _run_task_locked(name, spec, manual=False, admin=None)
        assert out["status"] == "failed"
        with get_session() as db:
            rows = (
                db.query(TaskRunLog)
                .filter(TaskRunLog.task_name == name)
                .order_by(TaskRunLog.id)
                .all()
            )
            observed = [(r.id, r.status) for r in rows]
            assert [r.status for r in rows] == [TaskRunLog.STATUS_FAILED], (
                f"运行记录必须收口成 1 条 failed；实际 {observed}"
                "（残留 running 会让看板连续失败恒为 1）"
            )
            assert rows[0].finished_at is not None
            assert "probe boom" in (rows[0].error or "")
    finally:
        with get_session() as db:
            db.query(TaskRunLog).filter(TaskRunLog.task_name == name).delete()
            db.commit()
