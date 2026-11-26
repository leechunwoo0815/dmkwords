// admin-web/src/pages/ServerMonitor.tsx — 管理端「服务器监控」（FEAT-091）
//
// 两套数据源：
//   · 实时（10 分钟）：GET /api/admin/system/live 每 10 秒轮询，浏览器内滚动 60 点；
//   · 长区间（30 分钟–24 小时）：GET /api/admin/system/history，服务端 60 秒采样聚合
//     （30m/1h=60s、6h=120s、12h/24h=300s），页面每 60 秒刷新一次。
// 关闭/切后台都停止轮询；KPI 与图表共用一次 live 请求，不重复打后端。

import {
  ApiOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  HddOutlined,
  ReloadOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Segmented } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiSystemHistory, apiSystemLive, type SystemLive } from "../api/admin";
import PageHead from "../components/PageHead";
import {
  Gauge,
  LineChart,
  StackedAreaChart,
  type AreaLayer,
  type LineSeries,
} from "../components/ServerMonitorCharts";
import "../styles/server-monitor.css";

const LIVE_INTERVAL_MS = 10_000;
const HISTORY_INTERVAL_MS = 60_000;
const MAX_POINTS = 60; // 10 分钟 × 每 10 秒一个点

type Tone = "ok" | "warn" | "down";
type ProcessRow = NonNullable<SystemLive["processes"]["top_cpu"]>[number];

/** 图表点：ts 用于区间裁剪，label 用于轴/提示框。 */
type ChartPoint = {
  ts: number;
  label: string;
  cpu: number;
  memApp: number;
  memCache: number;
  memFree: number;
  load1: number;
  load5: number;
  load15: number;
  rx: number;
  tx: number;
  diskRead: number;
  diskWrite: number;
  requests: number;
  errors: number;
  p95: number;
};

type RangeKey = "10m" | "30m" | "1h" | "6h" | "12h" | "24h";

const RANGES: {
  key: RangeKey;
  label: string;
  /** 请求服务端回看的小时数（10m 用浏览器实时窗口，不需要） */
  hours: number;
  /** 只保留最近 N 分钟（30m 视图从 1h 数据裁前一半） */
  trimMinutes: number;
}[] = [
  { key: "10m", label: "10 分钟", hours: 0, trimMinutes: 0 },
  { key: "30m", label: "30 分钟", hours: 1, trimMinutes: 30 },
  { key: "1h", label: "1 小时", hours: 1, trimMinutes: 0 },
  { key: "6h", label: "6 小时", hours: 6, trimMinutes: 0 },
  { key: "12h", label: "12 小时", hours: 12, trimMinutes: 0 },
  { key: "24h", label: "24 小时", hours: 24, trimMinutes: 0 },
];

function toneFor(value: number, warn: number, danger: number): Tone {
  if (value >= danger) return "down";
  if (value >= warn) return "warn";
  return "ok";
}

function statusTone(status: string): Tone {
  if (status === "ok") return "ok";
  if (status === "down") return "down";
  return "warn";
}

function pct(value: number, total: number): number {
  return total > 0 ? Math.max(0, Math.min(100, (value / total) * 100)) : 0;
}

function fmtUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours} 小时 ${minutes} 分` : `${minutes} 分`;
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

/** 10 分钟视图：历史回填点与实时点共用「时:分:秒」刻度。 */
function secondsClockLabel(ts: number): string {
  return new Date(ts).toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
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
    requests: data.app.requests_1m,
    errors: data.app.errors_4xx_1m + data.app.errors_5xx_1m,
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

function peakOf(points: ChartPoint[], pick: (p: ChartPoint) => number) {
  let value = 0;
  let idx = -1;
  points.forEach((p, i) => {
    const v = pick(p);
    if (v > value) {
      value = v;
      idx = i;
    }
  });
  return { value, idx, label: idx >= 0 ? (points[idx]?.label ?? "—") : "—" };
}

function avgOf(points: ChartPoint[], pick: (p: ChartPoint) => number): number {
  if (points.length === 0) return 0;
  return points.reduce((sum, p) => sum + pick(p), 0) / points.length;
}

function Legend({ items }: { items: { name: string; color: string }[] }) {
  return (
    <div className="sm-legend">
      {items.map((it) => (
        <span key={it.name}>
          <span className="sm-legend-dot" style={{ background: it.color }} />
          {it.name}
        </span>
      ))}
    </div>
  );
}

function Metric({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="sm-metric">
      <div className="sm-metric-label">{label}</div>
      <div className="sm-metric-value">
        {value}
        {unit ? <span className="sm-metric-unit">{unit}</span> : null}
      </div>
    </div>
  );
}

function ProcessList({
  title,
  rows,
  unit,
  value,
}: {
  title: string;
  rows: ProcessRow[];
  unit: string;
  value: (row: ProcessRow) => number;
}) {
  const max = Math.max(1, ...rows.map(value));
  return (
    <div>
      <div className="sm-proc-title">{title}</div>
      {rows.length === 0 ? (
        <div className="chart-empty">暂无数据</div>
      ) : (
        rows.map((row) => (
          <div className="sm-proc-row" key={`${title}-${row.pid}`}>
            <span className="sm-proc-name" title={row.name}>
              {row.name}
            </span>
            <span className="sm-proc-track">
              <span className="sm-proc-fill" style={{ width: `${(value(row) / max) * 100}%` }} />
            </span>
            <span className="sm-proc-val">
              {value(row).toFixed(1)} {unit}
            </span>
          </div>
        ))
      )}
    </div>
  );
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
  const labels = points.map((p) => p.label);

  const cpu = live?.cpu.percent ?? 0;
  const memPct = live ? pct(live.memory.used_mb, live.memory.total_mb) : 0;
  const diskPct = live?.disk.percent ?? 0;
  const appErrors = live ? live.app.errors_4xx_1m + live.app.errors_5xx_1m : 0;

  const attention: string[] = [];
  if (live) {
    if (cpu >= 90) attention.push("CPU");
    if (memPct >= 90) attention.push("内存");
    if (diskPct >= 85) attention.push("磁盘");
    if (live.cpu.load1 > live.host.cpu_count * 1.5) attention.push("负载");
    if (live.services.mysql !== "ok") attention.push("MySQL");
    // 本地开发环境没有 nginx（生产才有）——不计入整页“需关注”，服务行单独注明
    if (live.services.nginx !== "ok" && !import.meta.env.DEV) attention.push("nginx");
    if (!live.services.scheduler.running) attention.push("调度器");
  }
  const overall: Tone = !live ? "warn" : attention.length === 0 ? "ok" : attention.length >= 2 ? "down" : "warn";
  const overallText = !live
    ? "正在加载服务器状态…"
    : overall === "ok"
      ? "整体资源正常"
      : `有 ${attention.length} 项需要关注`;

  const cpuSeries: LineSeries[] = [
    { name: "CPU", color: "var(--sm-orange)", values: points.map((p) => p.cpu), unit: "%", digits: 1 },
  ];
  const loadSeries: LineSeries[] = [
    { name: "1 分钟", color: "var(--sm-blue)", values: points.map((p) => p.load1), digits: 2 },
    { name: "5 分钟", color: "var(--sm-green)", values: points.map((p) => p.load5), digits: 2 },
    { name: "15 分钟", color: "var(--sm-purple)", values: points.map((p) => p.load15), digits: 2 },
  ];
  const memLayers: AreaLayer[] = [
    { name: "应用", color: "var(--sm-green)", values: points.map((p) => p.memApp) },
    { name: "缓存", color: "var(--sm-gold)", values: points.map((p) => p.memCache) },
    { name: "空闲", color: "var(--sm-cream)", values: points.map((p) => p.memFree) },
  ];
  const netSeries: LineSeries[] = [
    { name: "入站", color: "var(--sm-blue)", values: points.map((p) => p.rx), unit: "kB/s", digits: 1 },
    { name: "出站", color: "var(--sm-orange)", values: points.map((p) => p.tx), unit: "kB/s", digits: 1 },
  ];
  const diskSeries: LineSeries[] = [
    { name: "读", color: "var(--sm-purple)", values: points.map((p) => p.diskRead), unit: "kB/s", digits: 1 },
    { name: "写", color: "var(--sm-gold)", values: points.map((p) => p.diskWrite), unit: "kB/s", digits: 1 },
  ];
  const appSeries: LineSeries[] = [
    { name: "请求", color: "var(--sm-blue)", values: points.map((p) => p.requests), unit: "次/分", digits: 1 },
    { name: "错误", color: "var(--sm-red)", values: points.map((p) => p.errors), unit: "次/分", digits: 1 },
  ];
  const p95Series: LineSeries[] = [
    { name: "P95", color: "var(--sm-purple)", values: points.map((p) => p.p95), unit: "ms", digits: 0 },
  ];
  // 计数/毫秒刻度取整：避免出现「0.3 次请求」「0.6 ms」这种刻度
  const reqMax = Math.max(1, ...points.map((p) => p.requests));
  const reqTop = Math.max(4, Math.ceil(reqMax / 4) * 4);
  const p95Max = Math.max(1, ...points.map((p) => p.p95));
  const p95Top = Math.max(100, Math.ceil(p95Max / 25) * 25);

  // 区间统计：均值/峰值是运维判断“是否在恶化”的第一眼信息
  const cpuPeak = peakOf(points, (p) => p.cpu);
  const memPeak = peakOf(points, (p) => p.memApp);
  const loadPeak = peakOf(points, (p) => p.load1);
  const reqPeak = peakOf(points, (p) => p.requests);
  const spanMinutes =
    points.length > 1
      ? Math.round(((points[points.length - 1]?.ts ?? 0) - (points[0]?.ts ?? 0)) / 60_000)
      : 0;
  const requestedMinutes = isLiveRange ? 10 : rangeConfig.trimMinutes || rangeConfig.hours * 60;
  const coverageShort = points.length > 1 && spanMinutes < requestedMinutes * 0.8;
  const stepLabel = isLiveRange
    ? "10 秒"
    : historyStep >= 300
      ? "5 分钟"
      : historyStep >= 120
        ? "2 分钟"
        : "1 分钟";

  const serviceRows = [
    {
      label: "后端 dmkwords-backend",
      status: live?.services.backend ?? "—",
      detail: live ? `进程 ${live.process_count}` : "",
    },
    {
      label: "MySQL",
      status: live?.services.mysql ?? "—",
      detail: live ? `${live.mysql.connections} 连接 / ${live.mysql.running} 运行中` : "",
    },
    {
      label: "nginx",
      status: live?.services.nginx ?? "—",
      detail: live && live.services.nginx !== "ok" && import.meta.env.DEV ? "本地开发环境无 nginx" : "",
    },
    {
      label: "调度器",
      status: live?.services.scheduler.running ? "ok" : live?.services.scheduler.enabled ? "warn" : "down",
      detail: live ? `单实例锁 ${live.services.scheduler.has_lock_conn ? "已持有" : "未持有"}` : "",
    },
  ];

  return (
    <div className="sm-page">
      <PageHead
        title="服务器监控"
        desc={
          live
            ? `${live.host.hostname} · ${live.host.os} · KPI 每 10 秒自动刷新`
            : "后端所在服务器 · KPI 每 10 秒自动刷新"
        }
        extra={
          <>
            <span className="sm-updated">
              {updatedAt ? `更新于 ${updatedAt.toLocaleTimeString("zh-CN", { hour12: false })}` : "加载中…"}
            </span>
            <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={refreshAll}>
              刷新
            </Button>
          </>
        }
      />

      {liveError && (
        <Alert
          className="sm-alert"
          type={live ? "warning" : "error"}
          showIcon
          message={live ? "实时刷新失败，显示上次数据" : "实时数据加载失败"}
          description={liveError}
        />
      )}

      <div className="sm-kpi-grid">
        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-orange)" }}>
              <CloudServerOutlined />
            </span>
            CPU
          </div>
          <div className="kpi-tile-value">
            {live ? cpu.toFixed(1) : "—"}
            <span className="kpi-tile-unit">%</span>
          </div>
          <div className="kpi-tile-hint">
            负载{" "}
            {live
              ? `${live.cpu.load1.toFixed(2)} / ${live.cpu.load5.toFixed(2)} / ${live.cpu.load15.toFixed(2)}`
              : "—"}
          </div>
          <Gauge value={cpu} tone={toneFor(cpu, 70, 90)} label="CPU" />
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-green)" }}>
              <DatabaseOutlined />
            </span>
            内存
          </div>
          <div className="kpi-tile-value">
            {live ? memPct.toFixed(0) : "—"}
            <span className="kpi-tile-unit">%</span>
          </div>
          <div className="kpi-tile-hint">
            {live ? (
              <>
                已用 {live.memory.used_mb.toLocaleString()} / 可见 {live.memory.total_mb.toLocaleString()} MiB
                <br />
                可用 {live.memory.available_mb.toLocaleString()} MiB
                {live.memory.swap_used_mb > 0 && (
                  <>
                    <br />
                    Swap {live.memory.swap_used_mb.toLocaleString()} /{" "}
                    {live.memory.swap_total_mb.toLocaleString()} MiB
                  </>
                )}
              </>
            ) : (
              "—"
            )}
          </div>
          <Gauge value={memPct} tone={toneFor(memPct, 80, 92)} label="内存" />
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-gold)" }}>
              <HddOutlined />
            </span>
            磁盘
          </div>
          <div className="kpi-tile-value">
            {live ? diskPct.toFixed(0) : "—"}
            <span className="kpi-tile-unit">%</span>
          </div>
          <div className="kpi-tile-hint">
            {live ? (
              <>
                {live.disk.used_gb.toFixed(1)} / {live.disk.total_gb.toFixed(1)} GB
                <br />
                剩余 {live.disk.free_gb.toFixed(1)} GB
              </>
            ) : (
              "—"
            )}
          </div>
          <Gauge value={diskPct} tone={toneFor(diskPct, 80, 90)} label="磁盘" />
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-blue)" }}>
              <WifiOutlined />
            </span>
            网络
          </div>
          <div className="kpi-tile-value">
            {live ? live.network.rx_kbps.toFixed(1) : "—"}
            <span className="kpi-tile-unit">kB/s 入</span>
          </div>
          <div className="kpi-tile-hint">
            {live ? (
              <>
                入站 {live.network.rx_kbps.toFixed(1)} kB/s
                <br />
                出站 {live.network.tx_kbps.toFixed(1)} kB/s
              </>
            ) : (
              "—"
            )}
          </div>
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-purple)" }}>
              <ClockCircleOutlined />
            </span>
            运行时长
          </div>
          <div className="kpi-tile-value">{live ? fmtUptime(live.host.uptime_seconds) : "—"}</div>
          <div className="kpi-tile-hint">
            {live ? `${live.host.cpu_count} 核 · ${live.process_count} 进程` : "—"}
          </div>
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--sm-red)" }}>
              <ApiOutlined />
            </span>
            应用请求
          </div>
          <div className="kpi-tile-value">
            {live ? live.app.requests_1m : "—"}
            <span className="kpi-tile-unit">次/分</span>
          </div>
          <div className="kpi-tile-hint">
            {live ? `错误 ${appErrors} · P95 ${live.app.p95_ms_1m} ms` : "—"}
          </div>
        </div>
      </div>

      <div className={`sm-conclusion sm-conclusion--${overall}`}>
        <div className="sm-conclusion-main">
          <strong>{overallText}</strong>
          <span>
            CPU {live ? cpu.toFixed(1) : "—"}% · 内存 {live ? memPct.toFixed(0) : "—"}% · 磁盘{" "}
            {live ? diskPct.toFixed(0) : "—"}% · 负载 {live ? live.cpu.load1.toFixed(2) : "—"}
          </span>
        </div>
        {attention.length > 0 && (
          <span className="sm-conclusion-attention">关注：{attention.join("、")}</span>
        )}
      </div>

      <div className="sm-range-bar">
        <Segmented
          value={range}
          onChange={(v) => setRange(v as RangeKey)}
          options={RANGES.map((r) => ({ value: r.key, label: r.label }))}
        />
        <div className="sm-range-meta">
          {isLiveRange
            ? "已回填最近 10 分钟（服务端 60 秒采样）· 之后每 10 秒追加实时点"
            : historyLoading && historyPoints.length === 0
              ? "正在读取历史采样…"
              : `服务端 60 秒采样 · ${stepLabel}粒度 · 共 ${points.length} 个点${
                  coverageShort ? ` · 当前仅覆盖 ${spanMinutes} 分钟` : ""
                }`}
          {usingFallback ? " · 历史为空，暂显示本次页面数据" : ""}
        </div>
      </div>

      {historyError && !isLiveRange && (
        <Alert
          className="sm-alert"
          type="warning"
          showIcon
          message="历史数据加载失败"
          description={`${historyError}（图表暂显示本次页面数据）`}
        />
      )}

      <div className="sm-range-stats">
        <span className="sm-stat">
          <span className="sm-stat-label">CPU 均值</span>
          <span className="sm-stat-value">
            {points.length ? `${avgOf(points, (p) => p.cpu).toFixed(1)}%` : "—"}
          </span>
          <span className="sm-stat-label">峰值</span>
          <span className="sm-stat-value sm-stat-value--warn">
            {points.length ? `${cpuPeak.value.toFixed(1)}% @ ${cpuPeak.label}` : "—"}
          </span>
        </span>
        <span className="sm-stat">
          <span className="sm-stat-label">内存均值</span>
          <span className="sm-stat-value">
            {points.length ? `${avgOf(points, (p) => p.memApp).toFixed(1)}%` : "—"}
          </span>
          <span className="sm-stat-label">峰值</span>
          <span className="sm-stat-value sm-stat-value--warn">
            {points.length ? `${memPeak.value.toFixed(1)}% @ ${memPeak.label}` : "—"}
          </span>
        </span>
        <span className="sm-stat">
          <span className="sm-stat-label">负载峰值</span>
          <span className="sm-stat-value">{points.length ? loadPeak.value.toFixed(2) : "—"}</span>
          <span className="sm-stat-label">请求峰值</span>
          <span className="sm-stat-value">
            {points.length ? `${reqPeak.value.toFixed(1)} 次/分` : "—"}
          </span>
        </span>
      </div>

      <div className="sm-chart-grid">
        <Card
          size="small"
          className="dash-cell sm-cell"
          title="CPU 使用率"
          extra={<Legend items={[{ name: "CPU", color: "var(--sm-orange)" }]} />}
        >
          <LineChart labels={labels} series={cpuSeries} yMax={100} height={250} ariaLabel="CPU 使用率趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="内存构成"
          extra={
            <Legend
              items={[
                { name: "应用", color: "var(--sm-green)" },
                { name: "缓存", color: "var(--sm-gold)" },
                { name: "空闲", color: "var(--sm-cream)" },
              ]}
            />
          }
        >
          <StackedAreaChart labels={labels} layers={memLayers} height={250} ariaLabel="内存构成趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="系统负载"
          extra={
            <Legend
              items={[
                { name: "1 分钟", color: "var(--sm-blue)" },
                { name: "5 分钟", color: "var(--sm-green)" },
                { name: "15 分钟", color: "var(--sm-purple)" },
              ]}
            />
          }
        >
          <LineChart labels={labels} series={loadSeries} height={250} ariaLabel="系统负载趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="网络吞吐"
          extra={
            <Legend
              items={[
                { name: "入站", color: "var(--sm-blue)" },
                { name: "出站", color: "var(--sm-orange)" },
              ]}
            />
          }
        >
          <LineChart labels={labels} series={netSeries} height={250} ariaLabel="网络吞吐趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="磁盘 I/O"
          extra={
            <Legend
              items={[
                { name: "读", color: "var(--sm-purple)" },
                { name: "写", color: "var(--sm-gold)" },
              ]}
            />
          }
        >
          <LineChart labels={labels} series={diskSeries} height={250} ariaLabel="磁盘 I/O 趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="应用请求 / 错误"
          extra={
            <Legend
              items={[
                { name: "请求", color: "var(--sm-blue)" },
                { name: "错误", color: "var(--sm-red)" },
              ]}
            />
          }
        >
          <LineChart
            labels={labels}
            series={appSeries}
            yMax={reqTop}
            yInteger
            height={250}
            ariaLabel="应用请求与错误趋势"
          />
          <div className="sm-chart-note">
            今日请求 {live?.app.requests_today ?? "—"} · 今日错误 {live?.app.errors_today ?? "—"}
          </div>
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell sm-span-2"
          title="应用 P95 延迟"
          extra={<Legend items={[{ name: "P95", color: "var(--sm-purple)" }]} />}
        >
          <LineChart
            labels={labels}
            series={p95Series}
            yMax={p95Top}
            yInteger
            height={250}
            ariaLabel="应用 P95 延迟趋势"
          />
          <div className="sm-chart-note">
            最近 1 分钟 P95 {live?.app.p95_ms_1m ?? "—"} ms（监控页自身请求不计入）
          </div>
        </Card>
      </div>

      <div className="sm-bottom-grid">
        <Card size="small" className="dash-cell sm-cell sm-bottom-span" title="服务状态">
          <div className="sm-status">
            {serviceRows.map((row) => (
              <div className="sm-status-row" key={row.label}>
                <span>{row.label}</span>
                <span className={`sm-status-value sm-status-value--${statusTone(row.status)}`}>
                  <span className={`sm-status-dot sm-status-dot--${statusTone(row.status)}`} />
                  {row.status === "ok"
                    ? "正常"
                    : row.status === "down"
                      ? "异常"
                      : row.status === "warn"
                        ? "注意"
                        : row.status}
                  {row.detail ? <span className="sm-status-detail">{row.detail}</span> : null}
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card size="small" className="dash-cell sm-cell" title="MySQL">
          <div className="sm-metrics">
            <Metric label="连接数" value={live?.mysql.connections ?? "—"} />
            <Metric label="运行中" value={live?.mysql.running ?? "—"} />
            <Metric label="QPS" value={live ? live.mysql.qps.toFixed(1) : "—"} />
            <Metric label="慢查询" value={live?.mysql.slow_queries ?? "—"} />
          </div>
        </Card>

        <Card size="small" className="dash-cell sm-cell" title="应用（今日）">
          <div className="sm-metrics">
            <Metric label="请求" value={live?.app.requests_today ?? "—"} unit="次" />
            <Metric label="错误" value={live?.app.errors_today ?? "—"} unit="次" />
            <Metric label="近 1 分钟" value={live?.app.requests_1m ?? "—"} unit="次" />
            <Metric label="P95" value={live?.app.p95_ms_1m ?? "—"} unit="ms" />
          </div>
        </Card>
      </div>

      <Card size="small" className="dash-cell sm-cell sm-process-card" title="进程 Top（实时）">
        <div className="sm-proc-grid">
          <ProcessList
            title="CPU Top 5"
            rows={live?.processes.top_cpu ?? []}
            unit="%"
            value={(row) => row.cpu_percent}
          />
          <ProcessList
            title="内存 Top 5"
            rows={live?.processes.top_memory ?? []}
            unit="MiB"
            value={(row) => row.rss_mb}
          />
        </div>
      </Card>

      <div className="sm-footer">
        采样式只读监控：主机数据来自 psutil，MySQL 来自只读状态查询，应用指标来自进程内中间件（排除
        <code>/health</code> 与 <code>/api/admin/system/*</code>）。KPI 每 10 秒刷新；图表可选 10
        分钟/30 分钟/1 小时/6 小时/12 小时/24 小时，进入页面即用服务端 60 秒采样回填所选区间
        （10 分钟视图同样先回填、之后每 10 秒追加实时点；长区间粒度 1/2/5 分钟），鼠标悬停可读取该时刻全部指标。数据保留 24 小时。
      </div>
    </div>
  );
}
