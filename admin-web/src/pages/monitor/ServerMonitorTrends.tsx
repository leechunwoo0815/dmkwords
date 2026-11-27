// admin-web/src/pages/monitor/ServerMonitorTrends.tsx — 监控页中段：区间选择条 + 区间统计 + 趋势图
//
// 从 pages/ServerMonitor.tsx 原样搬出（B5-3 纯结构拆分，行为零变化）。
// 本文件同时是**区间/图表口径的家**：RANGES（回看小时数与裁剪分钟）、RangeKey、ChartPoint
// 由父页取数与这里画图共用一份定义——分开写两份必然漂移（30m 靠 trimMinutes 从 1h 裁一半，
// 父页若拿不到同一份契约就会多请求或少裁）。
// 统计口径（均值/峰值/覆盖不足/采样停摆）也在这里算：它们只服务于图表区的文案。

import { WarningOutlined } from "@ant-design/icons";
import { Alert, Card, Segmented } from "antd";

import { type SystemLive } from "../../api/admin";
import {
  LineChart,
  StackedAreaChart,
  type AreaLayer,
  type LineSeries,
} from "../../components/ServerMonitorCharts";

/** 图表点：ts 用于区间裁剪，label 用于轴/提示框。 */
export type ChartPoint = {
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

export type RangeKey = "10m" | "30m" | "1h" | "6h" | "12h" | "24h";

export const RANGES: {
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

export type ServerMonitorTrendsProps = {
  range: RangeKey;
  onRangeChange: (next: RangeKey) => void;
  /** 当前视图的点（已按区间裁剪、label 已按视距改写） */
  points: ChartPoint[];
  isLiveRange: boolean;
  historyLoading: boolean;
  /** 长区间下服务端历史是否为空（回退到本页实时点） */
  historyEmpty: boolean;
  historyError: string;
  usingFallback: boolean;
  /** 服务端采样粒度（秒），只用于文案「1/2/5 分钟粒度」 */
  historyStep: number;
  live: SystemLive | null;
};

export function ServerMonitorTrends({
  range,
  onRangeChange,
  points,
  isLiveRange,
  historyLoading,
  historyEmpty,
  historyError,
  usingFallback,
  historyStep,
  live,
}: ServerMonitorTrendsProps) {
  const rangeConfig = RANGES.find((r) => r.key === range) ?? RANGES[0]!;
  const labels = points.map((p) => p.label);

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
  // 数据新鲜度：采样任务停了曲线不会报错、只会停止生长，页面必须能分辨"刚部署"与"采样死了"
  const lastSampleAgeMin = points.length
    ? (Date.now() - (points[points.length - 1]?.ts ?? 0)) / 60_000
    : 0;
  const sampleStale = points.length > 0 && lastSampleAgeMin > 3;
  const stepLabel = isLiveRange
    ? "10 秒"
    : historyStep >= 300
      ? "5 分钟"
      : historyStep >= 120
        ? "2 分钟"
        : "1 分钟";

  return (
    <>
      <div className="sm-range-bar">
        <Segmented
          value={range}
          onChange={(v) => onRangeChange(v as RangeKey)}
          options={RANGES.map((r) => ({ value: r.key, label: r.label }))}
        />
        <div className="sm-range-meta">
          {isLiveRange
            ? "已回填最近 10 分钟（服务端 60 秒采样）· 之后每 10 秒追加实时点"
            : historyLoading && historyEmpty
              ? "正在读取历史采样…"
              : `服务端 60 秒采样 · ${stepLabel}粒度 · 共 ${points.length} 个点${
                  coverageShort ? ` · 当前仅覆盖 ${spanMinutes} 分钟` : ""
                }`}
          {usingFallback ? " · 历史为空，暂显示本次页面数据" : ""}
          {sampleStale && (
            <span className="sm-range-stale">
              <WarningOutlined /> 最近采样 {lastSampleAgeMin.toFixed(0)} 分钟前，采样可能已停止
            </span>
          )}
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
            本窗口 P95 {live?.app.p95_ms_1m ?? "—"} ms（窗口 {live?.app.window_seconds.toFixed(0) ?? "—"}s，监控页自身请求不计入）
          </div>
        </Card>
      </div>
    </>
  );
}
