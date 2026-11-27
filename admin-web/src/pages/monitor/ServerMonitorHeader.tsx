// admin-web/src/pages/monitor/ServerMonitorHeader.tsx — 监控页顶部：标题 + KPI 六宫格 + 结论条
//
// 从 pages/ServerMonitor.tsx 原样搬出（B5-3 纯结构拆分，行为零变化）。
// 本文件只吃一份 live 快照，自己推导「需关注」清单与整体结论——页面不必为纯展示再算一遍。
// 顺带导出 KPI 与取数共用的数值口径（pct / perMinute）：两处各写一份迟早漂移。

import {
  ApiOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  HddOutlined,
  ReloadOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { Alert, Button } from "antd";

import { type SystemLive } from "../../api/admin";
import PageHead from "../../components/PageHead";
import { Gauge } from "../../components/ServerMonitorCharts";

/** 健康分档：ok 正常 / warn 注意 / down 异常（KPI 仪表与服务状态行共用）。 */
export type Tone = "ok" | "warn" | "down";

function toneFor(value: number, warn: number, danger: number): Tone {
  if (value >= danger) return "down";
  if (value >= warn) return "warn";
  return "ok";
}

/** 占比（0–100）；总量为 0 时给 0，避免 NaN 渗进图表与仪表。 */
export function pct(value: number, total: number): number {
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

/**
 * 把「距上次轮转以来的原始计数」折算成每分钟速率。
 * 采样任务每 60 秒轮转一次窗口，所以原始计数是 0–60 秒锯齿：
 * 轮转刚过只有几秒的量，直接标「次/分」会低估（甚至显示 0 次/分但有流量）。
 */
export function perMinute(count: number, windowSeconds: number): number {
  const win = Math.max(1, windowSeconds || 60);
  return Math.round((count / win) * 60 * 10) / 10;
}

export type ServerMonitorHeaderProps = {
  live: SystemLive | null;
  liveError: string;
  updatedAt: Date | null;
  refreshing: boolean;
  onRefresh: () => void;
};

export function ServerMonitorHeader({
  live,
  liveError,
  updatedAt,
  refreshing,
  onRefresh,
}: ServerMonitorHeaderProps) {
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

  return (
    <>
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
            <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh}>
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
            {live ? Math.round(perMinute(live.app.requests_1m, live.app.window_seconds)) : "—"}
            <span className="kpi-tile-unit">次/分</span>
          </div>
          <div className="kpi-tile-hint">
            {live
              ? `错误 ${Math.round(perMinute(appErrors, live.app.window_seconds))} 次/分 · P95 ${
                  live.app.p95_ms_1m
                } ms（本窗口 ${live.app.window_seconds.toFixed(0)}s：${live.app.requests_1m} 次）`
              : "—"}
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
    </>
  );
}
