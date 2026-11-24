// admin-web/src/pages/ServerMonitor.tsx — 管理端「服务器监控」（FEAT-091）
//
// 数据链：GET /api/admin/system/live（10 秒轮询）+ /history（24h，5 分钟聚合，5 分钟刷新）。
// 关闭/切后台停止轮询；服务端 60 秒采样独立运行（24h 历史需要）。
// 图形全部手写 SVG + 主题令牌，不引图表库；风格对齐 DashboardCharts。

import {
  ApiOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  HddOutlined,
  ReloadOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card } from "antd";
import { useCallback, useEffect, useState } from "react";

import { apiSystemHistory, apiSystemLive, type SystemHistory, type SystemLive } from "../api/admin";
import PageHead from "../components/PageHead";
import { Gauge, LineChart, StackedAreaChart, type AreaLayer, type LineSeries } from "../components/ServerMonitorCharts";
import "../styles/server-monitor.css";

const LIVE_INTERVAL_MS = 10_000;
const HISTORY_INTERVAL_MS = 300_000;

type Tone = "ok" | "warn" | "down";
type HistoryPoint = NonNullable<SystemHistory["points"]>[number];
type ProcessRow = NonNullable<SystemLive["processes"]["top_cpu"]>[number];

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
  const [history, setHistory] = useState<SystemHistory | null>(null);
  const [liveError, setLiveError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const loadLive = useCallback(async () => {
    try {
      const data = await apiSystemLive();
      setLive(data);
      setLiveError("");
      setUpdatedAt(new Date());
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : "实时数据加载失败");
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await apiSystemHistory(24);
      setHistory(data);
      setHistoryError("");
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "历史数据加载失败");
    }
  }, []);

  useEffect(() => {
    void loadLive();
    void loadHistory();
    const liveTimer = window.setInterval(() => {
      if (!document.hidden) void loadLive();
    }, LIVE_INTERVAL_MS);
    const historyTimer = window.setInterval(() => {
      if (!document.hidden) void loadHistory();
    }, HISTORY_INTERVAL_MS);
    const onVisible = () => {
      if (!document.hidden) {
        void loadLive();
        void loadHistory();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(liveTimer);
      window.clearInterval(historyTimer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [loadLive, loadHistory]);

  const refreshAll = async () => {
    setRefreshing(true);
    await Promise.allSettled([loadLive(), loadHistory()]);
    setRefreshing(false);
  };

  const points: HistoryPoint[] = history?.points ?? [];
  const labels = points.map((p) => p.at.slice(11, 16));
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
    { name: "CPU", color: "var(--chart-1)", values: points.map((p) => p.cpu_percent) },
  ];
  const memLayers: AreaLayer[] = [
    {
      name: "应用",
      color: "var(--chart-2)",
      values: points.map((p) => pct(p.mem_used_mb, p.mem_total_mb)),
    },
    {
      name: "缓存",
      color: "var(--chart-4)",
      values: points.map((p) => pct(p.mem_cached_mb, p.mem_total_mb)),
    },
    {
      name: "空闲",
      color: "var(--color-bg-subtle)",
      values: points.map((p) =>
        Math.max(0, 100 - pct(p.mem_used_mb, p.mem_total_mb) - pct(p.mem_cached_mb, p.mem_total_mb)),
      ),
    },
  ];
  const netSeries: LineSeries[] = [
    { name: "入站", color: "var(--chart-1)", values: points.map((p) => p.net_rx_kbps) },
    { name: "出站", color: "var(--chart-3)", values: points.map((p) => p.net_tx_kbps) },
  ];
  const diskSeries: LineSeries[] = [
    { name: "读", color: "var(--chart-3)", values: points.map((p) => p.disk_read_kbps) },
    { name: "写", color: "var(--chart-6)", values: points.map((p) => p.disk_write_kbps) },
  ];
  const appSeries: LineSeries[] = [
    { name: "请求", color: "var(--chart-1)", values: points.map((p) => p.app_requests) },
    { name: "错误", color: "var(--color-danger)", values: points.map((p) => p.app_4xx + p.app_5xx) },
  ];
  const p95Series: LineSeries[] = [
    { name: "P95", color: "var(--chart-5)", values: points.map((p) => p.app_p95_ms) },
  ];
  // 计数/毫秒刻度取整：避免出现「0.3 次请求」「0.6 ms」这种刻度
  const reqMax = Math.max(1, ...points.map((p) => p.app_requests));
  const reqTop = Math.max(4, Math.ceil(reqMax / 4) * 4);
  const p95Max = Math.max(1, ...points.map((p) => p.app_p95_ms));
  const p95Top = Math.max(100, Math.ceil(p95Max / 25) * 25);

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
    { label: "nginx", status: live?.services.nginx ?? "—", detail: live && live.services.nginx !== "ok" && import.meta.env.DEV ? "本地开发环境无 nginx" : "" },
    {
      label: "调度器",
      status: live?.services.scheduler.running
        ? "ok"
        : live?.services.scheduler.enabled
          ? "warn"
          : "down",
      detail: live
        ? `单实例锁 ${live.services.scheduler.has_lock_conn ? "已持有" : "未持有"}`
        : "",
    },
  ];

  return (
    <div className="sm-page">
      <PageHead
        title="服务器监控"
        desc={
          live
            ? `${live.host.hostname} · ${live.host.os} · 每 10 秒自动刷新`
            : "后端所在服务器 · 每 10 秒自动刷新"
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
      {historyError && (
        <Alert
          className="sm-alert"
          type="warning"
          showIcon
          message="24h 历史加载失败"
          description={historyError}
        />
      )}

      <div className="sm-kpi-grid">
        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--chart-1)" }}>
              <CloudServerOutlined />
            </span>
            CPU
          </div>
          <div className="kpi-tile-value">
            {live ? cpu.toFixed(1) : "—"}
            <span className="kpi-tile-unit">%</span>
          </div>
          <div className="kpi-tile-hint">
            负载 {live ? `${live.cpu.load1.toFixed(2)} / ${live.cpu.load5.toFixed(2)} / ${live.cpu.load15.toFixed(2)}` : "—"}
          </div>
          <Gauge value={cpu} tone={toneFor(cpu, 70, 90)} label="CPU" />
        </div>

        <div className="kpi-tile">
          <div className="kpi-tile-head">
            <span className="kpi-tile-icon" style={{ color: "var(--chart-2)" }}>
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
                {live.memory.used_mb.toLocaleString()} / {live.memory.total_mb.toLocaleString()} MiB
                <br />
                可用 {live.memory.available_mb.toLocaleString()} MiB
                {live.memory.swap_used_mb > 0 && (
                  <>
                    <br />
                    Swap {live.memory.swap_used_mb.toLocaleString()} / {live.memory.swap_total_mb.toLocaleString()} MiB
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
            <span className="kpi-tile-icon" style={{ color: "var(--chart-3)" }}>
              <HddOutlined />
            </span>
            磁盘 /
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
            <span className="kpi-tile-icon" style={{ color: "var(--chart-4)" }}>
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
            <span className="kpi-tile-icon" style={{ color: "var(--chart-5)" }}>
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
            <span className="kpi-tile-icon" style={{ color: "var(--chart-6)" }}>
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

      {points.length < 12 && (
        <div className="sm-chart-note sm-history-note">
          24h 历史从部署后开始积累，当前 {points.length} 个采样点（每 60 秒一个）；曲线会随时间补全。
        </div>
      )}

      <div className="sm-chart-grid">
        <Card
          size="small"
          className="dash-cell sm-cell"
          title="CPU 使用率（24h）"
          extra={<Legend items={[{ name: "CPU", color: "var(--chart-1)" }]} />}
        >
          <LineChart labels={labels} series={cpuSeries} height={250} ariaLabel="CPU 使用率 24 小时趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="内存构成（24h，占总量 %）"
          extra={
            <Legend
              items={[
                { name: "应用", color: "var(--chart-2)" },
                { name: "缓存", color: "var(--chart-4)" },
                { name: "空闲", color: "var(--color-bg-subtle)" },
              ]}
            />
          }
        >
          <StackedAreaChart labels={labels} layers={memLayers} height={250} ariaLabel="内存构成 24 小时趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="网络吞吐（24h，kB/s）"
          extra={
            <Legend
              items={[
                { name: "入站", color: "var(--chart-1)" },
                { name: "出站", color: "var(--chart-3)" },
              ]}
            />
          }
        >
          <LineChart labels={labels} series={netSeries} height={250} ariaLabel="网络吞吐 24 小时趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="磁盘 I/O（24h，kB/s）"
          extra={
            <Legend
              items={[
                { name: "读", color: "var(--chart-3)" },
                { name: "写", color: "var(--chart-6)" },
              ]}
            />
          }
        >
          <LineChart labels={labels} series={diskSeries} height={250} ariaLabel="磁盘 I/O 24 小时趋势" />
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="应用请求 / 错误（24h，5 分钟聚合）"
          extra={
            <Legend
              items={[
                { name: "请求", color: "var(--chart-1)" },
                { name: "错误", color: "var(--color-danger)" },
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
            ariaLabel="应用请求与错误 24 小时趋势"
          />
          <div className="sm-chart-note">
            今日请求 {live?.app.requests_today ?? "—"} · 今日错误 {live?.app.errors_today ?? "—"}
          </div>
        </Card>

        <Card
          size="small"
          className="dash-cell sm-cell"
          title="应用 P95 延迟（24h，ms）"
          extra={<Legend items={[{ name: "P95", color: "var(--chart-5)" }]} />}
        >
          <LineChart
            labels={labels}
            series={p95Series}
            yMax={p95Top}
            yInteger
            height={250}
            ariaLabel="应用 P95 延迟 24 小时趋势"
          />
          <div className="sm-chart-note">最近 1 分钟 P95 {live?.app.p95_ms_1m ?? "—"} ms（监控页自身请求不计入）</div>
        </Card>
      </div>

      <div className="sm-bottom-grid">
        <Card size="small" className="dash-cell sm-cell" title="服务状态">
          <div className="sm-status">
            {serviceRows.map((row) => (
              <div className="sm-status-row" key={row.label}>
                <span>{row.label}</span>
                <span className={`sm-status-value sm-status-value--${statusTone(row.status)}`}>
                  <span className={`sm-status-dot sm-status-dot--${statusTone(row.status)}`} />
                  {row.status === "ok" ? "正常" : row.status === "down" ? "异常" : row.status === "warn" ? "注意" : row.status}
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
        <code>/health</code> 与 <code>/api/admin/system/*</code>）。24h 历史每 60 秒采样、保留 24 小时。
      </div>
    </div>
  );
}
