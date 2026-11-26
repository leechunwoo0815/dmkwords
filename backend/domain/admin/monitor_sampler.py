# backend/domain/admin/monitor_sampler.py — 60 秒服务器指标采样
"""FEAT-091 的写入侧：调度器内部 job（不进 21 项任务看板）。

为什么不走 `TaskSpec`：60 秒一次若走任务注册表，每天会多 1,440 条 `TaskRunLog`，
管理端任务看板会被刷爆。这里直接挂 APScheduler 内部 job（`_start_jobs_now()` 注册，
首启与抢锁接管共用），仍受调度器单实例锁保护；失败只记日志，不写 TaskRunLog。

保留策略：每次采样顺带删除 24 小时前的行（1,440 行/天，带索引，成本可忽略）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from backend.database import get_session
from backend.domain.admin.models import SystemMetric
from backend.domain.admin.monitor_service import collect_host_metrics, collect_mysql
from backend.metrics import app_metrics

logger = logging.getLogger(__name__)

RETENTION_HOURS = 24


def register_metrics_job(scheduler) -> None:
    """挂 60 秒采样 job（调度器内部 job；**不进 21 项任务看板**，不写 TaskRunLog）。"""
    scheduler.add_job(
        sample_system_metrics,
        "interval",
        seconds=60,
        id="system_metrics_sample",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )


def sample_system_metrics() -> int:
    """写一行采样并清理过期行；返回 1=成功 0=失败（调度器不关心返回值）。"""
    # 逐次复核单实例锁（与 21 项任务的 guarded_run 对齐）：注册时持锁 ≠ 执行时还持锁。
    # 持锁副本的 `wait_timeout` 掐断空闲连接会让锁静默消失，另一个副本接管后若旧副本
    # 的采样 job 仍在跑，就会双写 system_metrics、历史曲线密度凭空翻倍。
    from backend.tasks.registry import (
        ensure_scheduler_lock,
    )  # 延迟导入：避免 registry↔sampler 模块级环

    if not ensure_scheduler_lock():
        logger.warning("system metrics sample skipped: scheduler lock not held")
        return 0

    session = get_session()
    try:
        host = collect_host_metrics()
        mysql = collect_mysql(session)
        app = app_metrics.snapshot_and_rotate()

        session.add(
            SystemMetric(
                collected_at=datetime.now(),
                cpu_percent=host["cpu"]["percent"],
                load1=host["cpu"]["load1"],
                load5=host["cpu"]["load5"],
                load15=host["cpu"]["load15"],
                mem_total_mb=host["memory"]["total_mb"],
                mem_used_mb=host["memory"]["used_mb"],
                mem_available_mb=host["memory"]["available_mb"],
                mem_cached_mb=host["memory"]["cached_mb"],
                swap_total_mb=host["memory"]["swap_total_mb"],
                swap_used_mb=host["memory"]["swap_used_mb"],
                disk_total_gb=host["disk"]["total_gb"],
                disk_used_gb=host["disk"]["used_gb"],
                disk_percent=host["disk"]["percent"],
                inode_percent=host["disk"]["inode_percent"],
                disk_read_kbps=host["disk"]["read_kbps"],
                disk_write_kbps=host["disk"]["write_kbps"],
                net_rx_kbps=host["network"]["rx_kbps"],
                net_tx_kbps=host["network"]["tx_kbps"],
                process_count=host["process_count"],
                uptime_seconds=host["host"]["uptime_seconds"],
                mysql_ok=1 if mysql["status"] == "ok" else 0,
                mysql_connections=mysql["connections"],
                mysql_running=mysql["running"],
                mysql_qps=mysql["qps"],
                mysql_slow_queries=mysql["slow_queries"],
                app_requests=app["requests"],
                app_4xx=app["errors_4xx"],
                app_5xx=app["errors_5xx"],
                app_p95_ms=app["p95_ms"],
            )
        )
        session.execute(
            delete(SystemMetric).where(
                SystemMetric.collected_at < datetime.now() - timedelta(hours=RETENTION_HOURS)
            )
        )
        session.commit()
        return 1
    except Exception:  # noqa: BLE001 — 采样失败不能影响业务任务/进程
        session.rollback()
        logger.exception("system metrics sample failed")
        return 0
    finally:
        session.close()
