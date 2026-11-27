// admin-web/src/pages/ServerMonitor.tsx — 管理端「服务器监控」（FEAT-091）：取数/轮询 + 页面装配
//
// 两套数据源：
//   · 实时（10 分钟）：GET /api/admin/system/live 每 10 秒轮询，浏览器内滚动 60 点；
//   · 长区间（30 分钟–24 小时）：GET /api/admin/system/history，服务端 60 秒采样聚合
//     （30m/1h=60s、6h=120s、12h/24h=300s），页面每 60 秒刷新一次。
// 关闭/切后台都停止轮询；KPI 与图表共用一次 live 请求，不重复打后端。
//
// 结构（B5-3 纯结构拆分，行为零变化）：本文件只保留**取数与装配**——
// 顶部 KPI → monitor/ServerMonitorHeader，区间/统计/趋势图 → monitor/ServerMonitorTrends，
// 服务/MySQL/应用/进程卡 → monitor/ServerMonitorTables。

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiSystemHistory, apiSystemLive, type SystemLive } from "../api/admin";
import { perMinute, pct, ServerMonitorHeader } from "./monitor/ServerMonitorHeader";
import { ServerMonitorTables } from "./monitor/ServerMonitorTables";
import { RANGES, type ChartPoint, type RangeKey, ServerMonitorTrends } from "./monitor/ServerMonitorTrends";
import "../styles/server-monitor.css";

const LIVE_INTERVAL_MS = 10_000;
const HISTORY_INTERVAL_MS = 60_000;
const MAX_POINTS = 60; // 10 分钟 × 每 10 秒一个点

/** 10 分钟视图：历史回填点与实时点共用「时:分:秒」刻度。 */
function secondsClockLabel(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function clockLabel(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 12/24 小时视图跨天，刻度带上日期，避免首尾都显示 01:55 分不清哪天。 */
function dateClockLabel(ts: number): string {
  const d = new Date(ts);
  const md = `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return `${md} ${clockLabel(ts)}`;
}

function liveToPoint(data: SystemLive): ChartPoint {
  const ts = new Date(data.server_time).getTime();
  const total = data.memory.total_mb;
  const memApp = pct(data.memory.used_mb, total);
  const memCache = pct(data.memory.cached_mb, total);
  return {
    ts,
    label: data.server_time.slice(11, 19),
    cpu: data.cpu.percent,
    memApp,
    memCache,
    memFree: Math.max(0, 100 - memApp - memCache),
    load1: data.cpu.load1,
    load5: data.cpu.load5,
    load15: data.cpu.load15,
    rx: data.network.rx_kbps,
    tx: data.network.tx_kbps,
    diskRead: data.disk.read_kbps,
    diskWrite: data.disk.write_kbps,
    requests: perMinute(data.app.requests_1m, data.app.window_seconds),
    errors: perMinute(data.app.errors_4xx_1m + data.app.errors_5xx_1m, data.app.window_seconds),
    p95: data.app.p95_ms_1m,
  };
}

type HistoryPoint = {
  at: string;
  cpu_percent: number;
  mem_used_mb: number;
  mem_total_mb: number;
  mem_cached_mb: number;
  load1: number;
  load5: number;
  load15: number;
  net_rx_kbps: number;
  net_tx_kbps: number;
  disk_read_kbps: number;
  disk_write_kbps: number;
  app_requests: number;
  app_4xx: number;
  app_5xx: number;
  app_p95_ms: number;
};

/** 历史点 → 图表点；请求/错误按分钟折算（bucket 是 60/120/300 秒的求和）。 */
function historyToPoint(point: HistoryPoint, stepSeconds: number): ChartPoint {
  const ts = new Date(point.at).getTime();
  const total = point.mem_total_mb;
  const memApp = pct(point.mem_used_mb, total);
  const memCache = pct(point.mem_cached_mb, total);
  const minutes = Math.max(1, stepSeconds / 60);
  return {
    ts,
    label: clockLabel(ts),
    cpu: point.cpu_percent,
    memApp,
    memCache,
    memFree: Math.max(0, 100 - memApp - memCache),
    load1: point.load1,
    load5: point.load5,
    load15: point.load15,
    rx: point.net_rx_kbps,
    tx: point.net_tx_kbps,
    diskRead: point.disk_read_kbps,
    diskWrite: point.disk_write_kbps,
    requests: Math.round((point.app_requests / minutes) * 10) / 10,
    errors: Math.round(((point.app_4xx + point.app_5xx) / minutes) * 10) / 10,
    p95: point.app_p95_ms,
  };
}

export default function ServerMonitor() {
  const [live, setLive] = useState<SystemLive | null>(null);
  const [livePoints, setLivePoints] = useState<ChartPoint[]>([]);
  const [liveError, setLiveError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [range, setRange] = useState<RangeKey>("10m");
  const [historyPoints, setHistoryPoints] = useState<ChartPoint[]>([]);
  const [historyStep, setHistoryStep] = useState(60);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyTick, setHistoryTick] = useState(0);

  const loadLive = useCallback(async () => {
    try {
      const data = await apiSystemLive();
      setLive(data);
      setLivePoints((prev) => {
        const point = liveToPoint(data);
        // 手动刷新可能落在 3 秒服务端缓存内，避免同一时间点重复
        if (prev.length > 0 && prev[prev.length - 1]?.label === point.label) return prev;
        return [...prev, point].slice(-MAX_POINTS);
      });
      setLiveError("");
      setUpdatedAt(new Date());
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : "实时数据加载失败");
    }
  }, []);

  /**
   * 10 分钟视图回填：进页面先用服务端最近 1 小时采样裁出最近 10 分钟，
   * 让首屏就是「已经发生的曲线」，而不是从打开页面才开始计时（服务端 60 秒一行）。
   * 之后实时点每 10 秒追加到同一数组，滚动窗口仍是最近 10 分钟。
   */
  const seedLiveWindow = useCallback(async () => {
    try {
      const data = await apiSystemHistory(1);
      const cut = Date.now() - 10 * 60_000;
      const seeded = (data.points ?? [])
        .map((p) => historyToPoint(p as HistoryPoint, data.step_seconds))
        .filter((p) => p.ts >= cut);
      if (seeded.length === 0) return;
      setLivePoints((prev) => {
        const byTs = new Map<number, ChartPoint>();
        for (const p of seeded) byTs.set(p.ts, { ...p, label: secondsClockLabel(p.ts) });
        for (const p of prev) byTs.set(p.ts, p);
        return [...byTs.values()].sort((a, b) => a.ts - b.ts).slice(-MAX_POINTS);
      });
    } catch {
      // 历史不可用（迁移未跑/DB 不可达）时退化为「只显示本次页面实时点」
    }
  }, []);

  useEffect(() => {
    void loadLive();
    void seedLiveWindow();
    const liveTimer = window.setInterval(() => {
      if (!document.hidden) void loadLive();
    }, LIVE_INTERVAL_MS);
    const onVisible = () => {
      if (!document.hidden) void loadLive();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(liveTimer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [loadLive, seedLiveWindow]);

  const rangeConfig = useMemo(
    () => RANGES.find((r) => r.key === range) ?? RANGES[0]!,
    [range],
  );

  // 长区间：拉服务端历史，60 秒续一次；切回 10 分钟就停，不浪费请求
  useEffect(() => {
    if (rangeConfig.hours === 0) {
      setHistoryError("");
      // 从长区间切回 10 分钟：补回离开期间的采样，曲线不留缺口
      void seedLiveWindow();
      return;
    }
    let cancelled = false;
    const load = async () => {
      setHistoryLoading(true);
      try {
        const data = await apiSystemHistory(rangeConfig.hours);
        if (cancelled) return;
        let points = (data.points ?? []).map((p) => historyToPoint(p as HistoryPoint, data.step_seconds));
        if (rangeConfig.hours >= 12) {
          points = points.map((p) => ({ ...p, label: dateClockLabel(p.ts) }));
        }
        if (rangeConfig.trimMinutes > 0) {
          const cut = Date.now() - rangeConfig.trimMinutes * 60_000;
          points = points.filter((p) => p.ts >= cut);
        }
        setHistoryPoints(points);
        setHistoryStep(data.step_seconds);
        setHistoryError("");
      } catch (e) {
        if (!cancelled) setHistoryError(e instanceof Error ? e.message : "历史数据加载失败");
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, HISTORY_INTERVAL_MS);
    const onVisible = () => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [rangeConfig, historyTick, seedLiveWindow]);

  const refreshAll = async () => {
    setRefreshing(true);
    if (rangeConfig.hours > 0) setHistoryTick((v) => v + 1);
    await loadLive();
    setRefreshing(false);
  };

  const isLiveRange = rangeConfig.hours === 0;
  // 历史为空（服务刚部署）时回退到本次页面的实时点，至少不是白板
  const points = isLiveRange ? livePoints : historyPoints.length > 0 ? historyPoints : livePoints;
  const usingFallback = !isLiveRange && historyPoints.length === 0 && livePoints.length > 0;

  return (
    <div className="sm-page">
      <ServerMonitorHeader
        live={live}
        liveError={liveError}
        updatedAt={updatedAt}
        refreshing={refreshing}
        onRefresh={refreshAll}
      />

      <ServerMonitorTrends
        range={range}
        onRangeChange={setRange}
        points={points}
        isLiveRange={isLiveRange}
        historyLoading={historyLoading}
        historyEmpty={historyPoints.length === 0}
        historyError={historyError}
        usingFallback={usingFallback}
        historyStep={historyStep}
        live={live}
      />

      <ServerMonitorTables live={live} />

      <div className="sm-footer">
        采样式只读监控：主机数据来自 psutil，MySQL 来自只读状态查询，应用指标来自进程内中间件（排除
        <code>/health</code> 与 <code>/api/admin/system/*</code>）。KPI 每 10 秒刷新；图表可选 10
        分钟/30 分钟/1 小时/6 小时/12 小时/24 小时，进入页面即用服务端 60 秒采样回填所选区间
        （10 分钟视图同样先回填、之后每 10 秒追加实时点；长区间粒度 1/2/5 分钟），鼠标悬停可读取该时刻全部指标。数据保留 24 小时。
      </div>
    </div>
  );
}
