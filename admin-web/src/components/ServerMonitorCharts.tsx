// admin-web/src/components/ServerMonitorCharts.tsx — 服务器监控页图形（手写 SVG，不引图表库）
//
// 与 DashboardCharts.tsx 同一套图形语言：平滑曲线、网格、末值标注、主题令牌。
// 这里只做监控页需要的三种图：折线（带面积填充 + 悬停读数）、堆叠面积、半圆仪表。
//
// 运维口径：
//   · 折线下方有同色渐变填充，曲线之间不会糊在一起（多序列填充更淡）；
//   · 鼠标悬停 = 竖向十字线 + 该时刻全部序列读数，便于定位尖峰；
//   · 峰值标注带数值，x 轴固定 5 个时间刻度（时间标签由调用方按期距格式化）。

import { useId, useState } from "react";

type Point = { x: number; y: number };

export type LineSeries = {
  name: string;
  color: string;
  values: number[];
  /** 提示框/末值的小数位；不传按量级自动 */
  digits?: number;
  /** 单位（% / kB/s / ms / 次/分），只用于提示框与末值 */
  unit?: string;
};

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

function valueText(v: number, digits?: number, unit?: string): string {
  const num = digits === undefined ? fmt(v) : v.toFixed(digits);
  if (!unit) return num;
  // 百分比紧贴数字（82.4%），其余单位留空格（12.5 kB/s）
  return unit === "%" ? `${num}%` : `${num} ${unit}`;
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

type TipRow = { name: string; color: string; value: string };

/** 悬停提示框（深色圆角卡片，避免与白色卡片对比不足）。 */
function Tooltip({
  x,
  y,
  width,
  title,
  rows,
}: {
  x: number;
  y: number;
  width: number;
  title: string;
  rows: TipRow[];
}) {
  const height = 22 + rows.length * 15;
  return (
    <g pointerEvents="none">
      <rect x={x} y={y} width={width} height={height} rx={6} fill="#23262b" opacity={0.94} />
      <text x={x + 9} y={y + 15} fill="#ffffff" opacity={0.82} fontSize={11} fontWeight={600}>
        {title}
      </text>
      {rows.map((row, i) => (
        <g key={row.name}>
          <rect x={x + 9} y={y + 25 + i * 15} width={6} height={6} rx={1.5} fill={row.color} />
          <text x={x + 20} y={y + 31 + i * 15} fill="#d9d9d9" fontSize={11}>
            {row.name}
          </text>
          <text x={x + width - 9} y={y + 31 + i * 15} fill="#ffffff" fontSize={11} textAnchor="end" fontWeight={600}>
            {row.value}
          </text>
        </g>
      ))}
    </g>
  );
}

/** 折线（带面积填充、悬停十字、峰值标注）；yMax 不传则按数据自动取整。 */
export function LineChart({
  labels,
  series,
  height = 250,
  yMax,
  yInteger = false,
  ariaLabel,
  fill = true,
}: {
  labels: string[];
  series: LineSeries[];
  height?: number;
  yMax?: number;
  yInteger?: boolean;
  ariaLabel: string;
  /** 是否画面积填充（默认 true） */
  fill?: boolean;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [hover, setHover] = useState<number | null>(null);

  const n = Math.min(labels.length, ...series.map((s) => s.values.length));
  if (n < 1) return <Empty />;
  // 单点（刚部署/历史被清空）按整幅宽度画水平线，避免“看起来是空图”
  const single = n === 1;
  const count = single ? 2 : n;
  const at = (s: LineSeries, i: number) => s.values[single ? 0 : i] ?? 0;

  const W = 640;
  const PAD_L = 46;
  const PAD_R = 16;
  const PAD_T = 16;
  const PAD_B = 26;
  const top = yMax ?? niceMax(Math.max(1, ...series.flatMap((s) => s.values.slice(0, n))) * 1.2);
  const stepX = (W - PAD_L - PAD_R) / Math.max(1, count - 1);
  const x = (i: number) => PAD_L + i * stepX;
  const y = (v: number) => height - PAD_B - (Math.max(0, Math.min(top, v)) / top) * (height - PAD_T - PAD_B);
  const baseY = height - PAD_B;
  const xTicks = single
    ? [0]
    : Array.from(
        new Set([
          0,
          Math.floor((count - 1) / 4),
          Math.floor((count - 1) / 2),
          Math.floor(((count - 1) * 3) / 4),
          count - 1,
        ]),
      );
  const peakValues = series[0]?.values.slice(0, n) ?? [];
  const peakIdx = peakValues.length ? peakValues.indexOf(Math.max(...peakValues)) : -1;
  const peakValue = peakIdx >= 0 ? (peakValues[peakIdx] ?? 0) : 0;
  const singleSeries = series.length === 1;

  const hoverLabel = hover === null ? "" : (labels[single ? 0 : hover] ?? "");
  const tipWidth = 150;
  const tipX =
    hover === null
      ? 0
      : Math.min(W - PAD_R - tipWidth, Math.max(PAD_L, x(hover) + 12 + tipWidth > W - PAD_R ? x(hover) - 12 - tipWidth : x(hover) + 12));
  const tipY = PAD_T + 2;

  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label={ariaLabel}>
        <defs>
          {series.map((s, si) => (
            <linearGradient key={s.name} id={`${uid}-fill-${si}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" style={{ stopColor: s.color, stopOpacity: singleSeries ? 0.46 : 0.24 }} />
              <stop offset="55%" style={{ stopColor: s.color, stopOpacity: singleSeries ? 0.18 : 0.1 }} />
              <stop offset="100%" style={{ stopColor: s.color, stopOpacity: 0.02 }} />
            </linearGradient>
          ))}
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(top * g)} y2={y(top * g)} className="chart-grid-line" />
            <text x={PAD_L - 8} y={y(top * g) + 3.5} textAnchor="end" className="chart-axis-text">
              {yInteger ? Math.round(top * g) : fmt(top * g)}
            </text>
          </g>
        ))}
        {xTicks.map((i) => (
          <text
            key={i}
            x={i === 0 ? PAD_L : i === count - 1 ? W - PAD_R : x(i)}
            y={height - 8}
            textAnchor={i === 0 ? "start" : i === count - 1 ? "end" : "middle"}
            className="chart-axis-text"
          >
            {labels[i]}
          </text>
        ))}

        {/* 面积填充：先铺在折线下面，多序列时淡一些避免糊成一片 */}
        {fill &&
          series.map((s, si) => {
            const pts = Array.from({ length: count }, (_, i) => ({ x: x(i), y: y(at(s, i)) }));
            const first = pts[0]!;
            const last = pts[pts.length - 1]!;
            const area = `${smoothPath(pts)} L${last.x.toFixed(1)},${baseY} L${first.x.toFixed(1)},${baseY} Z`;
            return <path key={`area-${s.name}`} d={area} fill={`url(#${uid}-fill-${si})`} stroke="none" />;
          })}

        {/* 峰值/最新竖向标注（顶部时间 + 数值） */}
        {!single && peakIdx >= 0 && peakValue > 0 && (
          <g>
            <line
              x1={x(peakIdx)}
              y1={PAD_T}
              x2={x(peakIdx)}
              y2={baseY}
              stroke="#b9a98d"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text
              x={x(peakIdx) + (peakIdx > count * 0.72 ? -6 : 6)}
              y={PAD_T + 11}
              textAnchor={peakIdx > count * 0.72 ? "end" : "start"}
              className="chart-axis-text"
            >
              峰值 {valueText(peakValue, series[0]?.digits, series[0]?.unit)} · {labels[peakIdx]}
            </text>
          </g>
        )}
        {single && (
          <g>
            <line
              x1={x(count - 1)}
              y1={PAD_T}
              x2={x(count - 1)}
              y2={baseY}
              stroke="#b9a98d"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text x={x(count - 1) - 6} y={PAD_T + 11} textAnchor="end" className="chart-axis-text">
              最新 {labels[0]}
            </text>
          </g>
        )}

        {series.map((s, si) => {
          const pts = Array.from({ length: count }, (_, i) => ({ x: x(i), y: y(at(s, i)) }));
          const last = pts[pts.length - 1]!;
          return (
            <g key={s.name}>
              <path d={smoothPath(pts)} fill="none" stroke={s.color} strokeWidth={2} strokeLinecap="round" />
              <circle cx={last.x} cy={last.y} r={3.5} fill={s.color} />
              <text
                x={W - PAD_R - 2}
                y={Math.max(tipY + 40, last.y - 6 - si * 12)}
                textAnchor="end"
                className="chart-peak-text"
                fill={s.color}
              >
                {valueText(at(s, count - 1), s.digits, s.unit)}
              </text>
            </g>
          );
        })}

        {/* 悬停：竖向十字 + 各序列读数；读数框自动避开右边缘 */}
        {hover !== null && (
          <g>
            <line
              x1={x(hover)}
              y1={PAD_T}
              x2={x(hover)}
              y2={baseY}
              stroke="var(--color-text-tertiary)"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.8}
            />
            {series.map((s) => (
              <circle
                key={`hover-${s.name}`}
                cx={x(hover)}
                cy={y(at(s, hover))}
                r={3}
                fill="#ffffff"
                stroke={s.color}
                strokeWidth={2}
              />
            ))}
            <Tooltip
              x={tipX}
              y={tipY}
              width={tipWidth}
              title={hoverLabel}
              rows={series.map((s) => ({
                name: s.name,
                color: s.color,
                value: valueText(at(s, hover), s.digits, s.unit),
              }))}
            />
          </g>
        )}

        {/* 透明捕获层：整个绘图区都能触发读数 */}
        <rect
          x={PAD_L}
          y={PAD_T}
          width={W - PAD_L - PAD_R}
          height={height - PAD_T - PAD_B}
          fill="transparent"
          onPointerMove={(e) => {
            const rect = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
            if (!rect || rect.width <= 0) return;
            const px = ((e.clientX - rect.left) / rect.width) * W;
            const idx = Math.round((px - PAD_L) / stepX);
            setHover(Math.max(0, Math.min(count - 1, idx)));
          }}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
    </div>
  );
}

/** 100% 堆叠面积（内存：应用/缓存/空闲），带悬停读数。 */
export function StackedAreaChart({
  labels,
  layers,
  height = 250,
  ariaLabel,
}: {
  labels: string[];
  layers: AreaLayer[];
  height?: number;
  ariaLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const n = Math.min(labels.length, ...layers.map((l) => l.values.length));
  if (n < 1) return <Empty />;
  // 单点按整幅宽度画堆叠带（否则多边形零宽度，首屏看不见）
  const single = n === 1;
  const count = single ? 2 : n;
  const at = (layer: AreaLayer, i: number) => layer.values[single ? 0 : i] ?? 0;

  const W = 640;
  const PAD_L = 46;
  const PAD_R = 16;
  const PAD_T = 16;
  const PAD_B = 26;
  const stepX = (W - PAD_L - PAD_R) / Math.max(1, count - 1);
  const x = (i: number) => PAD_L + i * stepX;
  const y = (v: number) => height - PAD_B - (v / 100) * (height - PAD_T - PAD_B);
  const baseY = height - PAD_B;
  const xTicks = single
    ? [0]
    : Array.from(
        new Set([
          0,
          Math.floor((count - 1) / 4),
          Math.floor((count - 1) / 2),
          Math.floor(((count - 1) * 3) / 4),
          count - 1,
        ]),
      );
  const cumulative = new Array<number>(count).fill(0);

  const tipWidth = 150;
  const tipX =
    hover === null
      ? 0
      : Math.min(W - PAD_R - tipWidth, Math.max(PAD_L, x(hover) + 12 + tipWidth > W - PAD_R ? x(hover) - 12 - tipWidth : x(hover) + 12));

  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} role="img" aria-label={ariaLabel}>
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(g)} y2={y(g)} className="chart-grid-line" />
            <text x={PAD_L - 8} y={y(g) + 3.5} textAnchor="end" className="chart-axis-text">
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
          return <path key={layer.name} d={d} fill={layer.color} fillOpacity={0.92} stroke="none" />;
        })}
        {xTicks.map((i) => (
          <text
            key={i}
            x={i === 0 ? PAD_L : i === count - 1 ? W - PAD_R : x(i)}
            y={height - 8}
            textAnchor={i === 0 ? "start" : i === count - 1 ? "end" : "middle"}
            className="chart-axis-text"
          >
            {labels[i]}
          </text>
        ))}

        {hover !== null && (
          <g>
            <line
              x1={x(hover)}
              y1={PAD_T}
              x2={x(hover)}
              y2={baseY}
              stroke="#ffffff"
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.85}
            />
            <Tooltip
              x={tipX}
              y={PAD_T + 2}
              width={tipWidth}
              title={labels[single ? 0 : hover] ?? ""}
              rows={layers.map((layer) => ({
                name: layer.name,
                color: layer.color,
                value: `${at(layer, hover).toFixed(1)}%`,
              }))}
            />
          </g>
        )}

        <rect
          x={PAD_L}
          y={PAD_T}
          width={W - PAD_L - PAD_R}
          height={height - PAD_T - PAD_B}
          fill="transparent"
          onPointerMove={(e) => {
            const rect = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
            if (!rect || rect.width <= 0) return;
            const px = ((e.clientX - rect.left) / rect.width) * W;
            const idx = Math.round((px - PAD_L) / stepX);
            setHover(Math.max(0, Math.min(count - 1, idx)));
          }}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
    </div>
  );
}

const TONE_COLOR: Record<"ok" | "warn" | "down", string> = {
  ok: "var(--sm-green)",
  warn: "var(--sm-gold)",
  down: "var(--sm-red)",
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
