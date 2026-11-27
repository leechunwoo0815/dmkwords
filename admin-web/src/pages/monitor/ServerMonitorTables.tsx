// admin-web/src/pages/monitor/ServerMonitorTables.tsx — 监控页底部：服务状态 / MySQL / 应用 / 进程 Top
//
// 从 pages/ServerMonitor.tsx 原样搬出（B5-3 纯结构拆分，行为零变化）。
// 这里全是"慢变量"（服务是否在跑、今日累计、进程快照），只需 live 一份快照，不碰轮询。

import { Card } from "antd";

import { type SystemLive } from "../../api/admin";
import { type Tone } from "./ServerMonitorHeader";

type ProcessRow = NonNullable<SystemLive["processes"]["top_cpu"]>[number];

function statusTone(status: string): Tone {
  if (status === "ok") return "ok";
  if (status === "down") return "down";
  return "warn";
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

export type ServerMonitorTablesProps = {
  live: SystemLive | null;
};

export function ServerMonitorTables({ live }: ServerMonitorTablesProps) {
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
    <>
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
            <Metric
              label={live ? `本窗口(${live.app.window_seconds.toFixed(0)}s)` : "本窗口"}
              value={live?.app.requests_1m ?? "—"}
              unit="次"
            />
            <Metric label="P95" value={live?.app.p95_ms_1m ?? "—"} unit="ms" />
          </div>
        </Card>
      </div>

      <Card size="small" className="dash-cell sm-cell sm-process-card" title="进程 Top（实时）">
        <div className="sm-proc-grid">
          <ProcessList
            title="CPU Top 5（单核 100% 计，可 >100%）"
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
    </>
  );
}
