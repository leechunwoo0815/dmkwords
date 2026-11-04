// admin-web/src/components/DashboardCharts.tsx — 仪表盘图形区（数据看板深度版）
//
// 沿革：
//   2026-09-21 甲方要「很酷炫的图，以后投到店外电视」→ 手写 SVG，不引图表库。
//   2026-11-04 第一百六十四批①把配色换成企业风；甲方反馈「图表的样式跟以前还一样、太简陋」，
//             ②本版重做图形语言，不再只是换色：
//   · 趋势图：Y 轴刻度 + 网格 + 平滑曲线 + 峰值标注 + 原生悬浮数值
//   · 健康度：半圆仪表盘（刻度 + 阈值配色），替代三个小圆环
//   · 馆藏构成：环形 + 图例百分比 + 中心总数
//   · 热门书：前三名徽章 + 渐变条 + 占比
//   · 小读者构成：堆叠条 + 百分比图例
//   · 数据新鲜度徽标（真实更新时间，不是装饰）
//
// 口径不变：数据全部来自既有 `GET /api/admin/dashboard`，不改后端、不动契约。
// 颜色全部来自主题令牌（`--chart-N` / 语义色），切主题图表自动跟随（见 styles/skins/paint.css）。

import { useCallback, useEffect, useState } from "react";
import { Card, Tooltip, Typography } from "antd";


import { apiDashboardCharts, type DashboardCharts as ChartsData } from "../api/admin";
import TechIcon from "./TechIcon";
import type { IconName } from "./TechIcon";

import type { components } from "../api/schema";

type Overview = components["schemas"]["DashboardOverviewResponse"];

const MEMBER_LABEL: Record<string, string> = {
  none: "未入会",
  observation: "观察期",
  pending_evaluation: "待评估",
  formal: "正式会员",
  expired: "已过期",
  withdrawn: "已退会",
};

const MEMBER_COLOR: Record<string, string> = {
  none: "var(--color-border-strong)",
  observation: "var(--chart-5)",
  pending_evaluation: "var(--color-warning)",
  formal: "var(--color-success)",
  expired: "var(--color-primary)",
  withdrawn: "var(--chart-4)",
};

const PALETTE = {
  available: "var(--chart-1)",
  borrowed: "var(--chart-3)",
  maintenance: "var(--chart-4)",
  lost: "var(--color-border-strong)",
  returned: "var(--color-success)",
};

type Point = { x: number; y: number };

/** 极坐标取点（0° 在 3 点方向，顺时针；仪表盘从 180° 画到 360°）。 */
function polar(cx: number, cy: number, r: number, deg: number): Point {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

/** 圆弧路径（角度制，闭合圆弧）。 */
function arcPath(cx: number, cy: number, r: number, from: number, to: number): string {
  const s = polar(cx, cy, r, from);
  const e = polar(cx, cy, r, to);
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M${s.x.toFixed(1)},${s.y.toFixed(1)} A${r},${r} 0 ${large} 1 ${e.x.toFixed(1)},${e.y.toFixed(1)}`;
}

/** Catmull-Rom → 三次贝塞尔；控制点限幅在相邻两点之间，防止在全 0 平段画出负值凹陷。 */
function smoothPath(points: Point[]): string {
  const first = points[0];
  if (!first) return "";
  if (points.length === 1) return `M${first.x.toFixed(1)},${first.y.toFixed(1)}`;
  let d = `M${first.x.toFixed(1)},${first.y.toFixed(1)}`;
  const clamp = (v: number, a: number, b: number) => Math.min(Math.max(v, Math.min(a, b)), Math.max(a, b));
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

/** 卡片标题：图标 + 文本（比纯文字更像"面板"）。 */
function CardTitle({ icon, text }: { icon: IconName; text: string }) {
  return (
    <span className="dash-card-title">
      <span className="dash-card-icon">
        <TechIcon name={icon} size={14} />
      </span>
      {text}
    </span>
  );
}

/* ------------------------------------------------------------------ 馆藏构成 */

function Donut({
  segments,
  total,
  title,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
  title: string;
}) {
  const R = 56;
  const W = 16;
  const C = 2 * Math.PI * R;
  const sum = segments.reduce((s, x) => s + x.value, 0) || 1;
  const drawn = segments.filter((s) => s.value > 0);
  let offset = 0;
  return (
    <div className="chart-donut">
      <svg viewBox="0 0 160 160" width={164} height={164} role="img" aria-label={title}>
        <defs>
          {drawn.map((s, i) => (
            <linearGradient key={s.label} id={`donut-grad-${i}`} gradientUnits="userSpaceOnUse" x1="18" y1="18" x2="142" y2="142">
              <stop offset="0%" stopColor={s.color} stopOpacity="1" />
              <stop offset="100%" stopColor={s.color} stopOpacity="0.55" />
            </linearGradient>
          ))}
        </defs>
        <circle cx="80" cy="80" r={R} fill="none" stroke="var(--color-bg-subtle)" strokeWidth={W} />
        {drawn.map((s, i) => {
          const seg = (s.value / sum) * C;
          const gap = drawn.length === 1 ? 0 : Math.min(6, seg * 0.2);
          const len = Math.max(seg - gap, 0);
          const el = (
            <circle
              key={s.label}
              cx="80"
              cy="80"
              r={R}
              fill="none"
              stroke={`url(#donut-grad-${i})`}
              strokeWidth={W}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-(offset + gap / 2)}
              transform="rotate(-90 80 80)"
            />
          );
          offset += seg;
          return el;
        })}
        <text x="80" y="78" textAnchor="middle" className="chart-center-num">
          {total}
        </text>
        <text x="80" y="98" textAnchor="middle" className="chart-center-sub">
          总藏书
        </text>
      </svg>
      <div className="chart-legend chart-legend--stacked">
        {segments.map((s) => (
          <Tooltip key={s.label} title={`${s.label} ${s.value} 本 · 占 ${Math.round((s.value / sum) * 100)}%`}>
            <div className="chart-legend-row">
              <span className="chart-dot" style={{ background: s.color }} />
              <span className="chart-legend-label">{s.label}</span>
              <span className="chart-legend-value">
                {s.value}
                <span className="chart-legend-pct">{Math.round((s.value / sum) * 100)}%</span>
              </span>
            </div>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 健康度仪表 */

function Gauge({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  // 阈值配色：<50 危险 / <80 警示 / 其余良好（续借率低、通过率低都该被看见）
  const color = pct >= 80 ? "var(--color-success)" : pct >= 50 ? "var(--color-warning)" : "var(--color-danger)";
  const cx = 60;
  const cy = 62;
  const R = 44;
  const ticks = Array.from({ length: 11 }, (_, i) => 180 + i * 18);
  return (
    <div className="gauge">
      <svg viewBox="0 0 120 76" width={104} height={66} role="img" aria-label={`${label} ${pct}%`}>
        {ticks.map((deg, i) => {
          const p1 = polar(cx, cy, R + 8, deg);
          const p2 = polar(cx, cy, R + (i % 5 === 0 ? 12 : 10), deg);
          return (
            <line
              key={deg}
              x1={p1.x}
              y1={p1.y}
              x2={p2.x}
              y2={p2.y}
              stroke="var(--color-border)"
              strokeWidth={i % 5 === 0 ? 1.6 : 1}
              strokeLinecap="round"
            />
          );
        })}
        <path d={arcPath(cx, cy, R, 180, 360)} fill="none" stroke="var(--color-bg-subtle)" strokeWidth={10} strokeLinecap="round" />
        {pct > 0 && (
          <path
            d={arcPath(cx, cy, R, 180, 180 + (pct / 100) * 180)}
            fill="none"
            stroke={color}
            strokeWidth={10}
            strokeLinecap="round"
          />
        )}
        <text x={cx} y={cy - 8} textAnchor="middle" className="gauge-num">
          {pct.toFixed(pct >= 10 ? 0 : 1)}
          <tspan className="gauge-unit">%</tspan>
        </text>
      </svg>
      <div className="gauge-label">{label}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ 趋势大图 */

function TrendChart({ points }: { points: { date: string; borrowed: number; returned: number }[] }) {
  const W = 680;
  const H = 210;
  const PAD_L = 34;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 24;
  if (!points.length) return <div className="chart-empty">暂无数据</div>;

  const max = Math.max(1, ...points.map((p) => Math.max(p.borrowed, p.returned)));
  const niceMax = max <= 4 ? 4 : Math.ceil(max / 2) * 2;
  const x = (i: number) => PAD_L + (i * (W - PAD_L - PAD_R)) / Math.max(1, points.length - 1);
  const y = (v: number) => H - PAD_B - (v / niceMax) * (H - PAD_T - PAD_B);
  const series = (key: "borrowed" | "returned") => points.map((p, i) => ({ x: x(i), y: y(p[key]) }));
  const borrowedPts = series("borrowed");
  const returnedPts = series("returned");
  const last = borrowedPts[borrowedPts.length - 1];
  const area = last
    ? `${smoothPath(borrowedPts)} L${last.x.toFixed(1)},${H - PAD_B} L${PAD_L},${H - PAD_B} Z`
    : "";

  // Y 轴：4 等分刻度
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((g) => ({ g, v: Math.round(niceMax * g) }));
  // X 轴：首/中/尾 + 峰值
  const peakIdx = points.reduce((best, p, i) => (p.borrowed > (points[best]?.borrowed ?? 0) ? i : best), 0);
  const peak = points[peakIdx];
  const bandWidth = (W - PAD_L - PAD_R) / Math.max(1, points.length - 1);

  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="借还趋势">
        <defs>
          <linearGradient id="trend-area" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={PALETTE.borrowed} stopOpacity="0.32" />
            <stop offset="100%" stopColor={PALETTE.borrowed} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t.g}>
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={y(niceMax * t.g)}
              y2={y(niceMax * t.g)}
              className="chart-grid-line"
            />
            <text x={PAD_L - 6} y={y(niceMax * t.g) + 3.5} textAnchor="end" className="chart-axis-text">
              {t.v}
            </text>
          </g>
        ))}
        <path d={area} fill="url(#trend-area)" stroke="none" />
        <path d={smoothPath(borrowedPts)} fill="none" stroke={PALETTE.borrowed} strokeWidth={2} strokeLinecap="round" />
        <path
          d={smoothPath(returnedPts)}
          fill="none"
          stroke={PALETTE.returned}
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeDasharray="5 4"
        />
        {/* 峰值标注：把"最高是哪天"直接写在图上，不用读者自己找 */}
        {peak && peak.borrowed > 0 && (
          <g>
            <circle cx={x(peakIdx)} cy={y(peak.borrowed)} r={3} fill={PALETTE.borrowed} stroke="var(--color-bg-container)" strokeWidth={1.6} />
            <text x={x(peakIdx)} y={y(peak.borrowed) - 8} textAnchor="middle" className="chart-peak-text">
              峰值 {peak.borrowed}
            </text>
          </g>
        )}
        {/* 悬浮区：原生 title，鼠标停在某天就能看到借出/归还 */}
        {points.map((p, i) => (
          <rect
            key={p.date}
            x={x(i) - bandWidth / 2}
            y={PAD_T}
            width={bandWidth}
            height={H - PAD_T - PAD_B}
            fill="transparent"
          >
            <title>{`${p.date}　借出 ${p.borrowed} · 归还 ${p.returned}`}</title>
          </rect>
        ))}
        {points.map((p, i) =>
          p.borrowed > 0 ? (
            <circle key={p.date} cx={x(i)} cy={y(p.borrowed)} r={2.2} fill={PALETTE.borrowed} />
          ) : null,
        )}
      </svg>
      <div className="chart-trend-axis">
        <span>{points[0]?.date}</span>
        <span>{points[Math.floor(points.length / 2)]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
      <div className="chart-trend-legend">
        <span className="chart-legend-row">
          <span className="chart-line-sample" style={{ background: PALETTE.borrowed }} />
          <span className="chart-legend-label">借出</span>
        </span>
        <span className="chart-legend-row">
          <span className="chart-line-sample chart-line-sample--dashed" style={{ borderColor: PALETTE.returned }} />
          <span className="chart-legend-label">归还</span>
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 热门书 */

function HotBooks({ items }: { items: { book_id: number; title: string; borrow_count: number }[] }) {
  if (!items.length) return <div className="chart-empty">近 30 天暂无借阅</div>;
  const max = Math.max(1, ...items.map((i) => i.borrow_count));
  const total = items.reduce((s, i) => s + i.borrow_count, 0) || 1;
  return (
    <div className="chart-hot">
      {items.map((it, idx) => (
        <div key={it.book_id} className="chart-hot-row">
          <span className={`chart-rank${idx < 3 ? ` chart-rank--${idx + 1}` : ""}`}>{idx + 1}</span>
          <span className="chart-hot-title" title={it.title}>
            {it.title}
          </span>
          <span className="chart-hot-track">
            <span className="chart-hot-fill" style={{ width: `${(it.borrow_count / max) * 100}%` }} />
          </span>
          <span className="chart-hot-num">
            {it.borrow_count}
            <span className="chart-hot-pct">{Math.round((it.borrow_count / total) * 100)}%</span>
          </span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ 小读者构成 */

function MemberMix({ items }: { items: { status: string; count: number }[] }) {
  const total = items.reduce((s, x) => s + x.count, 0);
  if (!total) return <div className="chart-empty">暂无会员档案</div>;
  return (
    <div className="chart-member">
      <div className="chart-member-bar">
        {items.map((m) => (
          <Tooltip key={m.status} title={`${MEMBER_LABEL[m.status] ?? m.status} ${m.count} 人`}>
            <span
              className="chart-member-seg"
              style={{
                width: `${(m.count / total) * 100}%`,
                background: MEMBER_COLOR[m.status] ?? "var(--color-border-strong)",
              }}
            />
          </Tooltip>
        ))}
      </div>
      <div className="chart-member-legend">
        {items.map((m) => (
          <span key={m.status} className="chart-legend-row">
            <span className="chart-dot" style={{ background: MEMBER_COLOR[m.status] ?? "var(--color-border-strong)" }} />
            <span className="chart-legend-label">{MEMBER_LABEL[m.status] ?? m.status}</span>
            <span className="chart-legend-value">{m.count}</span>
          </span>
        ))}
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        共 {total} 位小读者
      </Typography.Text>
    </div>
  );
}

/** 依赖聚合端点的图形占位（P2-23）：区分「加载中」与「加载失败 + 自动重试」。 */
function ChartsPlaceholder({ failed }: { failed: boolean }) {
  return <div className="chart-empty">{failed ? "图表加载失败 · 60 秒后自动重试" : "加载中…"}</div>;
}

/** 数据新鲜度徽标：显示真实的最近一次成功取数时间（不是装饰）。 */
function Freshness({ at }: { at: Date | null }) {
  const hhmmss = at
    ? `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}:${String(
        at.getSeconds(),
      ).padStart(2, "0")}`
    : "—";
  return (
    <span className="dash-freshness">
      <span className="dash-freshness-dot" aria-hidden />
      数据更新于 {hhmmss} · 每 60 秒自动刷新
    </span>
  );
}

export default function DashboardCharts({ data }: { data: Overview }) {
  const d = data as unknown as Record<string, number>;
  const [charts, setCharts] = useState<ChartsData | null>(null);
  const [chartsFailed, setChartsFailed] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(() => {
    apiDashboardCharts(14, 5)
      .then((r) => {
        setCharts(r);
        setChartsFailed(false);
        setUpdatedAt(new Date());
      })
      .catch(() => setChartsFailed(true));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [load]);

  const total =
    (d.copy_available ?? 0) + (d.copy_borrowed ?? 0) + (d.copy_maintenance ?? 0) + (d.copy_lost ?? 0);

  return (
    <div className="dash-grid">
      <Card
        size="small"
        className="dash-cell dash-span-2"
        title={<CardTitle icon="trend" text="近 14 天借还趋势" />}
        extra={<Freshness at={updatedAt} />}
      >
        {charts ? <TrendChart points={charts.borrow_trend ?? []} /> : <ChartsPlaceholder failed={chartsFailed} />}
      </Card>

      <Card
        size="small"
        className="dash-cell"
        title={<CardTitle icon="gauge" text="健康度" />}
        extra={<span className="dash-card-extra">优秀：续借≥60% · 通过≥80% · 退会≤10%</span>}
      >
        <div className="gauge-cluster">
          <Gauge label="续借率" value={d.renew_rate ?? 0} />
          <Gauge label="测验通过率" value={d.quiz_pass_rate ?? 0} />
          <Gauge label="退会率" value={d.withdrawal_rate ?? 0} />
        </div>
      </Card>

      <Card size="small" className="dash-cell" title={<CardTitle icon="pie" text="馆藏构成" />}>
        <Donut
          title="馆藏构成"
          total={total}
          segments={[
            { label: "在馆", value: d.copy_available ?? 0, color: PALETTE.available },
            { label: "借出", value: d.copy_borrowed ?? 0, color: PALETTE.borrowed },
            { label: "维护", value: d.copy_maintenance ?? 0, color: PALETTE.maintenance },
            { label: "遗失", value: d.copy_lost ?? 0, color: PALETTE.lost },
          ]}
        />
      </Card>

      <Card size="small" className="dash-cell" title={<CardTitle icon="rank" text="热门书 TOP5 · 近 30 天" />}>
        {charts ? <HotBooks items={charts.hot_books ?? []} /> : <ChartsPlaceholder failed={chartsFailed} />}
      </Card>

      <Card size="small" className="dash-cell" title={<CardTitle icon="group" text="小读者构成" />}>
        {charts ? <MemberMix items={charts.member_status ?? []} /> : <ChartsPlaceholder failed={chartsFailed} />}
      </Card>

    </div>
  );
}
