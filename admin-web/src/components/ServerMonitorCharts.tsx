// admin-web/src/components/ServerMonitorCharts.tsx — 服务器监控页图形（手写 SVG，不引图表库）
//
// 与 DashboardCharts.tsx 同一套图形语言：平滑曲线、网格、末值标注、主题令牌。
// 这里只做监控页需要的三种图：折线、堆叠面积、半圆仪表。

type Point = { x: number; y: number };

export type LineSeries = { name: string; color: string; values: number[] };
export type AreaLayer = { name: string; color: string; values: number[] };

function niceMax(value: number, minimum = 1): number {
  const v = Math.max(value, minimum);
  const exp = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]) {
    if (v <= m * exp) return m * exp;
  }
  return 10 * exp;
}

function fmt(v: number): string {
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(1);
  return v.toFixed(2).replace(/\.?0+$/, "");
}

function smoothPath(points: Point[]): string {
  const first = points[0];
  if (!first) return "";
  if (points.length === 1) return `M${first.x.toFixed(1)},${first.y.toFixed(1)}`;
  const clamp = (v: number, a: number, b: number) =>
    Math.min(Math.max(v, Math.min(a, b)), Math.max(a, b));
  let d = `M${first.x.toFixed(1)},${first.y.toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const prev = points[i - 1] ?? points[i]!;
    const cur = points[i]!;
    const next = points[i + 1]!;
    const after = points[i + 2] ?? next;
    const c1x = clamp(cur.x + (next.x - prev.x) / 6, cur.x, next.x);
    const c1y = clamp(cur.y + (next.y - prev.y) / 6, cur.y, next.y);
    const c2x = clamp(next.x - (after.x - cur.x) / 6, cur.x, next.x);
    const c2y = clamp(next.y - (after.y - cur.y) / 6, cur.y, next.y);
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${next.x.toFixed(1)},${next.y.toFixed(1)}`;
  }
  return d;
}

function Empty() {
  return <div className="chart-empty">暂无数据</div>;
}

/** 多序列折线（24h 历史）；yMax 不传则按数据自动取整。 */
export function LineChart({
  labels,
  series,
  height = 220,
  yMax,
  yInteger = false,
  ariaLabel,
}: {
  labels: string[];
  series: LineSeries[];
  height?: number;
  yMax?: number;
  yInteger?: boolean;
  ariaLabel: string;
}) {
  const n = Math.min(labels.length, ...series.map((s) => s.values.length));
  if (n < 1) return <Empty />;
  // 单点（刚部署/历史被清空）按整幅宽度画水平线，避免"看起来是空图"
  const single = n === 1;
  const count = single ? 2 : n;
  const at = (s: LineSeries, i: number) => s.values[single ? 0 : i] ?? 0;

  const W = 640;
  const PAD_L = 44;
  const PAD_R = 14;
  const PAD_T = 12;
  const PAD_B = 24;
  const top = yMax ?? niceMax(Math.max(1, ...series.flatMap((s) => s.values.slice(0, n))) * 1.2);
  const x = (i: number) => PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(1, count - 1);
  const y = (v: number) => height - PAD_B - (Math.max(0, Math.min(top, v)) / top) * (height - PAD_T - PAD_B);
  const xTicks = single ? [0] : Array.from(new Set([0, Math.floor(count / 2), count - 1]));

  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label={ariaLabel}>
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(top * g)} y2={y(top * g)} className="chart-grid-line" />
            <text x={PAD_L - 6} y={y(top * g) + 3.5} textAnchor="end" className="chart-axis-text">
              {yInteger ? Math.round(top * g) : fmt(top * g)}
            </text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text key={i} x={x(i)} y={height - 7} textAnchor="middle" className="chart-axis-text">
            {labels[i]}
          </text>
        ))}
        {/* 首序列面积：与单页看板一致的"有体积"曲线（弱透明，不遮网格） */}
        {series[0] && count > 1 && (
          <path
            d={`${smoothPath(
              Array.from({ length: count }, (_, i) => ({ x: x(i), y: y(at(series[0]!, i)) })),
            )} L${x(count - 1).toFixed(1)},${height - PAD_B} L${x(0).toFixed(1)},${height - PAD_B} Z`}
            fill={series[0].color}
            fillOpacity={0.09}
            stroke="none"
          />
        )}
        {series.map((s, si) => {
          const pts = Array.from({ length: count }, (_, i) => ({ x: x(i), y: y(at(s, i)) }));
          const last = pts[pts.length - 1]!;
          return (
            <g key={s.name}>
              <path d={smoothPath(pts)} fill="none" stroke={s.color} strokeWidth={2.2} strokeLinecap="round" />
              <circle cx={last.x} cy={last.y} r={2.8} fill={s.color} />
              <text
                x={W - PAD_R - 2}
                y={Math.max(10, last.y - 6 - si * 11)}
                textAnchor="end"
                className="chart-peak-text"
                fill={s.color}
              >
                {fmt(at(s, count - 1))}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** 100% 堆叠面积（内存：应用/缓存/空闲）。 */
export function StackedAreaChart({
  labels,
  layers,
  height = 220,
  ariaLabel,
}: {
  labels: string[];
  layers: AreaLayer[];
  height?: number;
  ariaLabel: string;
}) {
  const n = Math.min(labels.length, ...layers.map((l) => l.values.length));
  if (n < 1) return <Empty />;
  // 单点按整幅宽度画堆叠带（否则多边形零宽度，首屏看不见）
  const single = n === 1;
  const count = single ? 2 : n;
  const at = (layer: AreaLayer, i: number) => layer.values[single ? 0 : i] ?? 0;

  const W = 640;
  const PAD_L = 44;
  const PAD_R = 14;
  const PAD_T = 12;
  const PAD_B = 24;
  const x = (i: number) => PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(1, count - 1);
  const y = (v: number) => height - PAD_B - (v / 100) * (height - PAD_T - PAD_B);
  const xTicks = single ? [0] : Array.from(new Set([0, Math.floor(count / 2), count - 1]));
  const cumulative = new Array<number>(count).fill(0);

  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label={ariaLabel}>
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(g)} y2={y(g)} className="chart-grid-line" />
            <text x={PAD_L - 6} y={y(g) + 3.5} textAnchor="end" className="chart-axis-text">
              {g}%
            </text>
          </g>
        ))}
        {layers.map((layer) => {
          const bottom = [...cumulative];
          const topValues = Array.from({ length: count }, (_, i) => {
            const v = at(layer, i);
            const t = bottom[i]! + Math.max(0, Math.min(100 - bottom[i]!, v));
            cumulative[i] = t;
            return t;
          });
          const points = [
            ...bottom.map((v, i) => ({ x: x(i), y: y(v) })),
            ...topValues.map((v, i) => ({ x: x(i), y: y(v) })).reverse(),
          ];
          const d = `${points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")} Z`;
          return <path key={layer.name} d={d} fill={layer.color} fillOpacity={0.86} stroke="none" />;
        })}
        {xTicks.map((i) => (
          <text key={i} x={x(i)} y={height - 7} textAnchor="middle" className="chart-axis-text">
            {labels[i]}
          </text>
        ))}
      </svg>
    </div>
  );
}

const TONE_COLOR: Record<"ok" | "warn" | "down", string> = {
  ok: "var(--color-success)",
  warn: "var(--color-warning)",
  down: "var(--color-danger)",
};

/** 半圆仪表（0–100），用于 CPU/内存/磁盘。 */
export function Gauge({
  value,
  tone,
  label,
}: {
  value: number;
  tone: "ok" | "warn" | "down";
  label: string;
}) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const cx = 60;
  const cy = 58;
  const R = 42;
  const color = TONE_COLOR[tone];
  const arc = (from: number, to: number) => {
    const start = { x: cx + R * Math.cos((Math.PI * from) / 180), y: cy + R * Math.sin((Math.PI * from) / 180) };
    const end = { x: cx + R * Math.cos((Math.PI * to) / 180), y: cy + R * Math.sin((Math.PI * to) / 180) };
    const large = Math.abs(to - from) > 180 ? 1 : 0;
    return `M${start.x.toFixed(1)},${start.y.toFixed(1)} A${R},${R} 0 ${large} 1 ${end.x.toFixed(1)},${end.y.toFixed(1)}`;
  };
  return (
    <svg viewBox="0 0 120 70" width={112} height={66} role="img" aria-label={`${label} ${pct.toFixed(0)}%`}>
      <path d={arc(180, 360)} fill="none" stroke="var(--color-bg-subtle)" strokeWidth={9} strokeLinecap="round" />
      {pct > 0 && (
        <path
          d={arc(180, 180 + (pct / 100) * 180)}
          fill="none"
          stroke={color}
          strokeWidth={9}
          strokeLinecap="round"
        />
      )}
      <text x={cx} y={cy - 4} textAnchor="middle" className="gauge-num">
        {pct.toFixed(pct >= 10 ? 0 : 1)}
        <tspan className="gauge-unit">%</tspan>
      </text>
    </svg>
  );
}
