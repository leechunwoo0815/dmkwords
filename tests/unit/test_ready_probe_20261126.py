# tests/unit/test_ready_probe_20261126.py — B2-1 就绪探针（2026-11-26 双审查）
"""`/health` 恒 200 的语义是对的（进程活着），但它挡不住"进程活着、库连不上、定时任务全停"
这种**半死**状态：外部探针只看 `/health` 会一直绿灯，直到用户报错。

本批新增 `/ready`，判据三项（任一不过 → 503，响应体写明原因）：
  1. `db`：`SELECT 1`；
  2. `scheduler`：调度器在跑且握着锁连接（`SCHEDULER_ENABLED=false` 时不作判据）；
  3. `sampler`：`system_metrics` 最近一行 ≤ 5 分钟（同样仅调度器启用时判）。

测试环境不会真的起调度器（TestClient 不跑 lifespan），所以"正常态"用例用
`_force_scheduler_state()` 注入状态 + 造一行新鲜采样——顺手证明了判据确实在**读库与读状态**，
而不是恒真。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _force_scheduler_state(
    monkeypatch, *, enabled: bool = True, running: bool = True, has_lock: bool = True
) -> None:
    from backend.tasks import registry

    def _state() -> dict:
        return {"enabled": enabled, "running": running, "has_lock_conn": has_lock, "watchdog": True}

    monkeypatch.setattr(registry, "scheduler_state", _state)


def _seed_sample(db, *, minutes_ago: float) -> None:
    from backend.domain.admin.models import SystemMetric

    db.add(SystemMetric(collected_at=datetime.now() - timedelta(minutes=minutes_ago)))
    db.commit()


def test_ready_ok(client, db, monkeypatch):
    """库通 + 调度器在跑且有锁 + 采样新鲜 → 200。"""
    _force_scheduler_state(monkeypatch)
    _seed_sample(db, minutes_ago=0.5)
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"]["ok"] is True
    assert body["checks"]["scheduler"]["ok"] is True
    assert body["checks"]["sampler"]["ok"] is True


def test_ready_skips_sampler_when_scheduler_disabled(client, db, monkeypatch):
    """调度器关闭（本地开发形态）→ sampler 标 skipped 而不是判红。"""
    _force_scheduler_state(monkeypatch, enabled=False, running=False, has_lock=False)
    r = client.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checks"]["sampler"]["skipped"]


def test_ready_503_when_db_down(client, monkeypatch):
    """库不可用 → 503，且 sampler 直接标红（不再去查 MAX 重复报错）。"""
    import backend.main as main_mod

    _force_scheduler_state(monkeypatch)
    monkeypatch.setattr(
        main_mod, "_ready_db_check", lambda: (False, "OperationalError: connection refused")
    )
    r = client.get("/ready")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"]["ok"] is False
    assert "connection refused" in body["checks"]["db"]["error"]
    assert body["checks"]["sampler"]["ok"] is False


def test_ready_503_when_scheduler_enabled_but_not_running(client, monkeypatch):
    """调度器"启用却没在跑"（启动异常的真实形态）→ 503，且错误文案说清后果。"""
    _force_scheduler_state(monkeypatch, running=False, has_lock=False)
    r = client.get("/ready")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["checks"]["scheduler"]["ok"] is False
    assert "定时任务不会执行" in body["checks"]["scheduler"]["error"]


def test_ready_503_when_sampler_stale(client, db, monkeypatch):
    """采样过期（> 5 分钟）→ 503：看板显示"没数据"与"采样停了"不是一回事。"""
    _force_scheduler_state(monkeypatch)
    _seed_sample(db, minutes_ago=30)
    r = client.get("/ready")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["checks"]["sampler"]["ok"] is False
    assert body["checks"]["sampler"]["age_seconds"] > 300


def test_health_stays_200(client):
    """`/health` 语义不变：恒 200（进程活着），不因依赖异常改状态码。"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/ready", "/health"])
def test_probe_endpoints_not_counted_in_metrics(client, path):
    """探针请求不进应用指标（否则外部探针的每分钟请求量会把看板刷成假数据）。"""
    from backend.metrics import app_metrics

    before = app_metrics.snapshot()
    client.get(path)
    after = app_metrics.snapshot()
    assert after["requests"] == before["requests"]
