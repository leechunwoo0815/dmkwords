# backend/domain/admin/monitor_service.py — 服务器资源采集（FEAT-091）
"""管理端「服务器监控」的数据源：主机 / 服务 / 进程 / MySQL / 应用指标。

口径：
  · 只读：不写业务数据；采样写入走 `monitor_sampler.py`；
  · 不执行 shell：nginx 用 psutil 按进程名判断，MySQL 用 SQL 状态；
  · 进程只给 名称/PID/CPU%/RSS，**不读 cmdline/env**（防泄露密钥）；
  · 所有数值单位写在字段名里（_mb/_gb/_kbps/_seconds），前端不猜。

性能：
  · live 结果缓存 3 秒（多标签页/多管理员不重复采集）；
  · CPU 采样 `interval=0.1` 在 FastAPI 同步端点（线程池）里执行，不阻塞事件循环；
  · 磁盘/网络速率用上一次计数差值，首次为 0；进程 CPU 同理。
"""

from __future__ import annotations

import os
import platform
import socket
import sys
import threading
import time
from datetime import datetime, timedelta

import psutil
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.metrics import app_metrics

_LIVE_CACHE_TTL = 3.0
_HISTORY_CACHE_TTL = 60.0
_DISK_PATH = os.getenv("SYSTEM_MONITOR_DISK_PATH", "/")
# 历史聚合粒度候选（秒）。采样本身 60 秒一行，故最小 60：
#   1h → 60 秒（60 点）、6h → 120 秒（180 点）、12h/24h → 300 秒（144/288 点）。
_HISTORY_STEPS = (60, 120, 300)
_HISTORY_MAX_POINTS = 300

_cache_lock = threading.Lock()
#: 缓存过期瞬间可能有多名管理员同时请求：只让一个线程真正采集，其余等它出结果
_live_build_lock = threading.Lock()
_live_cache: tuple[float, dict] | None = None
_history_cache: dict[int, tuple[float, dict]] = {}

_rate_lock = threading.Lock()
_last_disk: tuple[float, int, int] | None = None
_last_net: tuple[float, int, int] | None = None
_last_mysql: tuple[float, int] | None = None
_proc_cpu: dict[int, tuple[float, float]] = {}


def _round(value: float, digits: int = 1) -> float:
    return round(float(value), digits)


def reset_caches() -> None:
    """测试用：清空实时/历史缓存与速率基线，避免跨用例串台。"""
    global _live_cache, _last_disk, _last_net, _last_mysql, _proc_cpu

    with _cache_lock:
        _live_cache = None
        _history_cache.clear()
    with _rate_lock:
        _last_disk = None
        _last_net = None
        _last_mysql = None
        _proc_cpu = {}


def _kb_rate(current: int, previous: int, elapsed: float) -> float:
    if elapsed <= 0:
        return 0.0
    return max(0.0, (current - previous) / elapsed / 1024.0)


def _rate_pair(
    now: float, previous: tuple[float, int, int] | None, cur1: int, cur2: int
) -> tuple[float, float, tuple[float, int, int]]:
    """双计数器速率（kB/s）+ 新基线。

    并发守卫（FEAT-091 深审 B1）：采集器与 live 请求线程共用基线，
    交错时可能出现「时间戳回退」或「计数器重置」。两种情况都返回 0 速率，
    并且**只接受时间前进的基线**，避免 elapsed 被钳到 1e-6 而产生假尖峰。
    """
    if previous is None:
        return 0.0, 0.0, (now, cur1, cur2)
    prev_at, prev1, prev2 = previous
    if now > prev_at and cur1 >= prev1 and cur2 >= prev2:
        elapsed = now - prev_at
        return (
            _round(_kb_rate(cur1, prev1, elapsed)),
            _round(_kb_rate(cur2, prev2, elapsed)),
            (now, cur1, cur2),
        )
    return 0.0, 0.0, ((now, cur1, cur2) if now > prev_at else previous)


def _auto_step(hours: int) -> int:
    """按回看时长选聚合粒度：在 60/120/300 秒里取“点数不超上限”的最小值。"""
    total = max(60, int(hours) * 3600)
    for step in _HISTORY_STEPS:
        if total / step <= _HISTORY_MAX_POINTS:
            return step
    return _HISTORY_STEPS[-1]


def _os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def collect_host_metrics() -> dict:
    """主机级快照（不查 DB、不依赖 FastAPI）。"""
    global _last_disk, _last_net

    per_core = psutil.cpu_percent(interval=0.1, percpu=True)
    cpu_percent = _round(sum(per_core) / len(per_core)) if per_core else 0.0
    load1, load5, load15 = os.getloadavg()

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    # 平台口径（与用户看到的系统工具对齐）：
    #   macOS：Activity Monitor / psutil.vm.percent 是 total - available（含压缩/不可回收部分）；
    #          开发机若用 vm.used 会显示 44%，与系统实际 70%+ 不符。cached 记 0（mac 无此口径）。
    #   Linux：与 `free -m` 完全对齐——used = total - free - buff/cache，
    #          cached 展示 buff/cache（psutil.cached 已含 SReclaimable，再加 Buffers）。
    #          注意不能用 psutil.vm.used：新版 psutil 的 used = total - available，
    #          它本身已包含可回收缓存，与 cached 相加会超过总量（页面堆叠图会顶到 100%+，
    #          生产 2G 机实测 61% + 45% = 106%，空闲被挤成 0）。
    if sys.platform == "darwin":
        used_bytes = max(0, int(vm.total) - int(vm.available))
        cached_bytes = 0
    else:
        cached_bytes = int(getattr(vm, "cached", 0)) + int(getattr(vm, "buffers", 0))
        used_bytes = max(0, int(vm.total) - int(getattr(vm, "free", 0)) - cached_bytes)
    disk = psutil.disk_usage(_DISK_PATH)
    try:
        statvfs = os.statvfs(_DISK_PATH)
        inode_percent = (1 - statvfs.f_ffree / statvfs.f_files) * 100 if statvfs.f_files else 0.0
    except OSError:
        inode_percent = 0.0

    # 采集器（60s 采样线程）与 live 端点（请求线程池）**共用同一组速率基线**：
    #   · 计数器读取、差值计算、基线写入必须在同一把锁内完成，否则两个线程交错时
    #     后读计数器的线程先写基线、先读的后写 → 基线时间戳回退；
    #   · 时间回退 / 计数器重置（重启、网卡重建）时不输出速率，且只接受更新的基线，
    #     避免 `elapsed` 被钳到 1e-6 而产生假尖峰或假 0。
    with _rate_lock:
        now = time.monotonic()
        disk_io = psutil.disk_io_counters()
        net_io = psutil.net_io_counters()
        disk_read = disk_io.read_bytes if disk_io else 0
        disk_write = disk_io.write_bytes if disk_io else 0
        net_rx = net_io.bytes_recv if net_io else 0
        net_tx = net_io.bytes_sent if net_io else 0
        disk_read_kbps, disk_write_kbps, _last_disk = _rate_pair(
            now, _last_disk, disk_read, disk_write
        )
        net_rx_kbps, net_tx_kbps, _last_net = _rate_pair(now, _last_net, net_rx, net_tx)

    return {
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "host": {
            "hostname": socket.gethostname(),
            "os": _os_name(),
            "kernel": platform.release(),
            "uptime_seconds": int(time.time() - psutil.boot_time()),
            "cpu_count": psutil.cpu_count(logical=True) or 1,
        },
        "cpu": {
            "percent": cpu_percent,
            "per_core": per_core,
            "load1": _round(load1, 2),
            "load5": _round(load5, 2),
            "load15": _round(load15, 2),
        },
        "memory": {
            "total_mb": int(vm.total / 1024 / 1024),
            "used_mb": int(used_bytes / 1024 / 1024),
            "available_mb": int(vm.available / 1024 / 1024),
            "cached_mb": int(cached_bytes / 1024 / 1024),
            "swap_total_mb": int(swap.total / 1024 / 1024),
            "swap_used_mb": int(swap.used / 1024 / 1024),
        },
        "disk": {
            "total_gb": _round(disk.total / 1024**3, 2),
            "used_gb": _round(disk.used / 1024**3, 2),
            "free_gb": _round(disk.free / 1024**3, 2),
            "percent": _round(disk.percent),
            "inode_percent": _round(inode_percent),
            "read_kbps": disk_read_kbps,
            "write_kbps": disk_write_kbps,
        },
        "network": {"rx_kbps": net_rx_kbps, "tx_kbps": net_tx_kbps},
        "process_count": len(psutil.pids()),
    }


def collect_processes() -> dict:
    """CPU Top5 + 内存 Top5；首次调用 CPU 差值未知会显示 0，下一次轮询即正常。"""
    global _proc_cpu

    now = time.monotonic()
    with _rate_lock:
        previous = dict(_proc_cpu)
        current: dict[int, tuple[float, float]] = {}

    rows: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            pid = proc.info["pid"]
            name = (proc.info["name"] or "?")[:40]
            times = proc.cpu_times()
            cpu_total = times.user + times.system
            prev_total, prev_at = previous.get(pid, (cpu_total, now))
            elapsed = max(now - prev_at, 1e-6)
            cpu_pct = max(0.0, (cpu_total - prev_total) / elapsed * 100)
            current[pid] = (cpu_total, now)
            mem_info = proc.info["memory_info"]
            rss_mb = (mem_info.rss if mem_info else 0) / 1024 / 1024
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "cpu_percent": _round(cpu_pct),
                    "rss_mb": _round(rss_mb),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    with _rate_lock:
        # 合并而非整表替换：采集器与 live 并发时，各自只更新自己看到的进程，
        # 整表替换会丢掉对方刚建立的基线 → 进程 CPU% 随机归零。
        _proc_cpu.update(current)
        stale = [pid for pid, (_, seen_at) in _proc_cpu.items() if now - seen_at > 300]
        for pid in stale:
            _proc_cpu.pop(pid, None)

    return {
        "top_cpu": sorted(rows, key=lambda r: r["cpu_percent"], reverse=True)[:5],
        "top_memory": sorted(rows, key=lambda r: r["rss_mb"], reverse=True)[:5],
    }


def collect_mysql(db: Session) -> dict:
    """MySQL 状态；不可达时返回 down + 0，并回滚会话以免污染后续查询。"""
    global _last_mysql

    try:
        result = db.execute(
            text(
                "SHOW GLOBAL STATUS WHERE Variable_name IN "
                "('Threads_connected','Threads_running','Questions','Slow_queries')"
            )
        ).all()
        stats = {row[0]: row[1] for row in result}
        questions = int(stats.get("Questions", 0))
        now = time.monotonic()
        with _rate_lock:
            qps = 0.0
            if _last_mysql is not None:
                prev_at, prev_questions = _last_mysql
                # 与磁盘/网络同款守卫：并发交错时时间戳可能回退，此时不输出速率也不回写旧基线
                if now > prev_at and questions >= prev_questions:
                    qps = _round((questions - prev_questions) / (now - prev_at), 2)
            if _last_mysql is None or now > _last_mysql[0] or questions < _last_mysql[1]:
                _last_mysql = (now, questions)
        return {
            "status": "ok",
            "connections": int(stats.get("Threads_connected", 0)),
            "running": int(stats.get("Threads_running", 0)),
            "qps": qps,
            "slow_queries": int(stats.get("Slow_queries", 0)),
        }
    except Exception:  # noqa: BLE001 — DB 挂了页面仍要出主机数据
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "down", "connections": 0, "running": 0, "qps": 0.0, "slow_queries": 0}


def collect_services(db: Session, mysql: dict) -> dict:
    try:
        from backend.tasks.registry import scheduler_state

        scheduler = scheduler_state()
    except Exception:  # noqa: BLE001
        scheduler = {"enabled": False, "running": False, "has_lock_conn": False, "watchdog": False}

    nginx_ok = False
    try:
        for proc in psutil.process_iter(["name"]):
            if (proc.info["name"] or "").lower() == "nginx":
                nginx_ok = True
                break
    except Exception:  # noqa: BLE001
        nginx_ok = False

    return {
        "backend": "ok",
        "mysql": mysql["status"],
        "nginx": "ok" if nginx_ok else "down",
        "scheduler": scheduler,
    }


def _today_totals(db: Session) -> dict:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        row = db.execute(
            text(
                "SELECT COALESCE(SUM(app_requests),0), COALESCE(SUM(app_4xx),0), "
                "COALESCE(SUM(app_5xx),0) FROM system_metrics WHERE collected_at >= :since"
            ),
            {"since": midnight},
        ).one()
        return {"requests_today": int(row[0]), "errors_today": int(row[1]) + int(row[2])}
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"requests_today": 0, "errors_today": 0}


class SystemMonitorService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _fresh_cache(self) -> dict | None:
        with _cache_lock:
            if _live_cache is not None and time.monotonic() - _live_cache[0] < _LIVE_CACHE_TTL:
                return _live_cache[1]
        return None

    def live(self) -> dict:
        global _live_cache

        cached = self._fresh_cache()
        if cached is not None:
            return cached

        # 双检 + 采集锁：缓存刚过期时并发请求只采集一次（多管理员/多标签页）
        with _live_build_lock:
            cached = self._fresh_cache()
            if cached is not None:
                return cached

            mysql = collect_mysql(self.db)
            host = collect_host_metrics()
            app_window = app_metrics.snapshot()
            today = _today_totals(self.db)
            data = {
                **host,
                "processes": collect_processes(),
                "mysql": mysql,
                "services": collect_services(self.db, mysql),
                "app": {
                    "requests_1m": app_window["requests"],
                    "errors_4xx_1m": app_window["errors_4xx"],
                    "errors_5xx_1m": app_window["errors_5xx"],
                    "p95_ms_1m": app_window["p95_ms"],
                    # 计数窗口起点由采样任务轮转决定：窗口 0–60 秒锯齿，前端按窗口长度折算「次/分」
                    "window_seconds": _round(app_window.get("window_seconds", 60.0), 1),
                    "requests_today": today["requests_today"],
                    "errors_today": today["errors_today"],
                },
            }
            with _cache_lock:
                _live_cache = (time.monotonic(), data)
            return data

    def history(self, hours: int = 24) -> dict:
        """回看 hours 小时历史；聚合粒度按时长自适应（1h=60s、6h=120s、12h/24h=300s）。"""
        hours = max(1, min(24, int(hours)))
        step = _auto_step(hours)
        now = time.monotonic()
        with _cache_lock:
            cached = _history_cache.get(hours)
            if cached is not None and now - cached[0] < _HISTORY_CACHE_TTL:
                return cached[1]

        since = datetime.now() - timedelta(hours=hours)
        points: list[dict] = []
        try:
            rows = self.db.execute(
                text(
                    "SELECT collected_at, cpu_percent, load1, load5, load15, mem_used_mb, mem_total_mb, "
                    "mem_cached_mb, disk_percent, net_rx_kbps, net_tx_kbps, "
                    "disk_read_kbps, disk_write_kbps, app_requests, app_4xx, app_5xx, app_p95_ms "
                    "FROM system_metrics WHERE collected_at >= :since ORDER BY collected_at"
                ),
                {"since": since},
            ).all()
            buckets: dict[int, list] = {}
            for row in rows:
                key = int(row[0].timestamp() // step)
                buckets.setdefault(key, []).append(row)
            for key in sorted(buckets):
                group = buckets[key]
                count = len(group)

                def avg(idx: int, _group=group, _count=count) -> float:
                    return _round(sum(float(r[idx] or 0) for r in _group) / _count, 2)

                points.append(
                    {
                        "at": datetime.fromtimestamp(key * step).isoformat(timespec="seconds"),
                        "cpu_percent": avg(1),
                        "load1": avg(2),
                        "load5": avg(3),
                        "load15": avg(4),
                        "mem_used_mb": int(avg(5)),
                        "mem_total_mb": int(avg(6)),
                        "mem_cached_mb": int(avg(7)),
                        "disk_percent": avg(8),
                        "net_rx_kbps": avg(9),
                        "net_tx_kbps": avg(10),
                        "disk_read_kbps": avg(11),
                        "disk_write_kbps": avg(12),
                        "app_requests": sum(int(r[13] or 0) for r in group),
                        "app_4xx": sum(int(r[14] or 0) for r in group),
                        "app_5xx": sum(int(r[15] or 0) for r in group),
                        "app_p95_ms": max(int(r[16] or 0) for r in group),
                    }
                )
        except Exception:  # noqa: BLE001 — 迁移未跑/DB 不可达时返回空曲线，不白屏
            try:
                self.db.rollback()
            except Exception:  # noqa: BLE001
                pass
            points = []

        data = {"hours": hours, "step_seconds": step, "points": points}
        with _cache_lock:
            _history_cache[hours] = (now, data)
        return data
