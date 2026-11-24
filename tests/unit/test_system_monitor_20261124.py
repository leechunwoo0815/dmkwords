# tests/unit/test_system_monitor_20261124.py — FEAT-091 服务器资源监控
"""覆盖：权限、响应形状/脱敏、应用指标中间件排除面、采样写入/24h 清理、历史聚合。"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.database import SessionLocal
from backend.domain.admin.models import SystemMetric
from backend.domain.admin.monitor_sampler import sample_system_metrics
from backend.domain.admin.monitor_service import SystemMonitorService
from backend.metrics import AppMetricsRegistry, app_metrics


def test_live_requires_auth_and_config_view(client, staff_headers):
    assert client.get("/api/admin/system/live").status_code == 401
    resp = client.get("/api/admin/system/live", headers=staff_headers)
    assert resp.status_code == 200, resp.text


def test_live_shape_and_no_sensitive_fields(client, admin_headers):
    resp = client.get("/api/admin/system/live", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body) == {
        "server_time",
        "host",
        "cpu",
        "memory",
        "disk",
        "network",
        "process_count",
        "processes",
        "mysql",
        "services",
        "app",
    }
    assert body["host"]["cpu_count"] >= 1
    assert body["memory"]["total_mb"] > 0
    assert body["disk"]["total_gb"] > 0
    assert set(body["app"]) == {
        "requests_1m",
        "errors_4xx_1m",
        "errors_5xx_1m",
        "p95_ms_1m",
        "requests_today",
        "errors_today",
    }

    # 进程只允许 4 个字段；命令行/env 绝不能出现在响应里
    for proc in body["processes"]["top_cpu"] + body["processes"]["top_memory"]:
        assert set(proc) == {"pid", "name", "cpu_percent", "rss_mb"}
    assert "cmdline" not in resp.text
    assert "environ" not in resp.text


def test_app_metrics_registry_p95_and_reset():
    registry = AppMetricsRegistry()
    for _ in range(100):
        registry.record(200, 8)
    for _ in range(5):
        registry.record(500, 1200)

    snap = registry.snapshot()
    assert snap["requests"] == 105
    assert snap["errors_4xx"] == 0
    assert snap["errors_5xx"] == 5
    # 100/105 在 10ms 桶内 → P95 = 10ms（固定桶估算）
    assert snap["p95_ms"] == 10

    rotated = registry.snapshot_and_rotate()
    assert rotated["requests"] == 105
    assert registry.snapshot()["requests"] == 0
    assert registry.snapshot()["p95_ms"] == 0


def test_app_metrics_middleware_excludes_monitor_endpoints(client, admin_headers):
    app_metrics.reset()
    resp = client.get("/api/admin/system/live", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert app_metrics.snapshot()["requests"] == 0, "监控页自身请求不能计入应用指标"

    resp = client.get("/api/miniapp/configs")
    assert resp.status_code == 200, resp.text
    assert app_metrics.snapshot()["requests"] >= 1


def test_sampler_writes_and_purges_24h():
    assert sample_system_metrics() == 1

    session = SessionLocal()
    try:
        row = session.query(SystemMetric).order_by(SystemMetric.id.desc()).first()
        assert row is not None
        assert row.mem_total_mb > 0
        assert row.collected_at is not None

        old = SystemMetric(collected_at=datetime.now() - timedelta(hours=25))
        session.add(old)
        session.commit()
        old_id = old.id
    finally:
        session.close()

    assert sample_system_metrics() == 1
    session = SessionLocal()
    try:
        assert session.get(SystemMetric, old_id) is None, "25 小时前的行必须被清理"
    finally:
        session.close()


def test_history_aggregates_5_min_buckets():
    base = datetime.now().replace(second=0, microsecond=0)
    session = SessionLocal()
    try:
        for i in range(3):
            session.add(
                SystemMetric(
                    collected_at=base + timedelta(seconds=i * 10),
                    cpu_percent=10 + i,
                    app_requests=2,
                    app_4xx=0,
                    app_5xx=0,
                    app_p95_ms=30,
                )
            )
        session.add(
            SystemMetric(
                collected_at=base - timedelta(minutes=5),
                cpu_percent=50,
                app_requests=7,
                app_p95_ms=90,
            )
        )
        session.commit()
    finally:
        session.close()

    session = SessionLocal()
    try:
        data = SystemMonitorService(session).history(24)
        assert data["step_seconds"] == 300
        assert len(data["points"]) == 2
        assert data["points"][-1]["app_requests"] == 6
        assert data["points"][-1]["cpu_percent"] == 11.0
        assert data["points"][0]["app_requests"] == 7
        assert data["points"][0]["app_p95_ms"] == 90
    finally:
        session.close()


def test_history_hours_clamped():
    session = SessionLocal()
    try:
        service = SystemMonitorService(session)
        assert service.history(99)["hours"] == 24
        assert service.history(0)["hours"] == 1
    finally:
        session.close()
