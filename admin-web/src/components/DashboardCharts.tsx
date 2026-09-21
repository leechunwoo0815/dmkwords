// admin-web/src/components/DashboardCharts.tsx — 仪表盘图形区（2026-09-21）
//
// 用户需求：「仪表盘不仅仅是数字，还有很多的图，很酷炫的图，让人眼前一亮，以后要投到店外的电视机上。」
// 用户同时裁定：**不新增页面**，就在仪表盘上融合（内部数据照留，投屏不影响）。
//
// 实现口径：
//  1. **零新依赖**——全部手写 SVG + CSS 动画（圆圆/柱/环形都只是几何），不引图表库：
//     绘本风的配色与描边要自己控，通用库默认皮肤"不像我们"；电视机常年开机也省内存与重绘。
//  2. 数据全部来自既有 `GET /api/admin/dashboard`（不改后端、不动契约）。
//  3. 颜色走 paint 令牌；大屏可读性：环宽 ≥ 22px、字号 ≥ 20px。
import { useCallback, useEffect, useState } from "react";
import { Card, Space, Tooltip, Typography } from "antd";

import { apiDashboardCharts, type DashboardCharts as ChartsData } from "../api/admin";

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
  none: "var(--paint-border)",
  observation: "var(--paint-blue)",
  pending_evaluation: "var(--paint-yellow)",
  formal: "var(--paint-secondary)",
  expired: "var(--paint-primary)",
  withdrawn: "var(--paint-pink)",
};

const PALETTE = {
  available: "var(--paint-secondary)", // 在馆
  borrowed: "var(--paint-primary)", // 借出
  maintenance: "var(--paint-yellow)", // 维护
  lost: "var(--paint-border)",
  returned: "var(--paint-secondary)", // 遗失
};

/** 甜甜圈：馆藏构成（在馆 / 借出 / 维护 / 遗失）。 */
function Donut({
  segments,
  total,
  title,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
  title: string;
}) {
  const R = 62;
  const W = 22;
  const C = 2 * Math.PI * R;
  const sum = segments.reduce((s, x) => s + x.value, 0) || 1;
  let offset = 0;
  return (
    <div className="chart-donut">
      <svg viewBox="0 0 170 170" width={170} height={170} role="img" aria-label={title}>
        <circle cx="85" cy="85" r={R} fill="none" stroke="var(--paint-paper-dim)" strokeWidth={W} />
        {segments.map((s) => {
          const len = (s.value / sum) * C;
          const el = (
            <circle
              key={s.label}
              cx="85"
              cy="85"
              r={R}
              fill="none"
              stroke={s.color}
              strokeWidth={W}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 85 85)"
            />
          );
          offset += len;
          return el;
        })}
        <text x="85" y="80" textAnchor="middle" className="chart-center-num">
          {total}
        </text>
        <text x="85" y="102" textAnchor="middle" className="chart-center-sub">
          总藏书
        </text>
      </svg>
      <div className="chart-legend">
        {segments.map((s) => (
          <Tooltip key={s.label} title={`${s.label} ${s.value} 本`}>
            <div className="chart-legend-row">
              <span className="chart-dot" style={{ background: s.color }} />
              <span className="chart-legend-label">{s.label}</span>
              <span className="chart-legend-value">{s.value}</span>
            </div>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}

/** 比率环：用于续借率 / 测验通过率 / 退会率（0–100）。 */
function RateRing({ label, value, color }: { label: string; value: number; color: string }) {
  const R = 42;
  const C = 2 * Math.PI * R;
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const len = (pct / 100) * C;
  return (
    <div className="chart-ring">
      <svg viewBox="0 0 110 110" width={110} height={110} role="img" aria-label={`${label} ${pct}%`}>
        <circle cx="55" cy="55" r={R} fill="none" stroke="var(--paint-paper-dim)" strokeWidth={14} />
        {/* 0 值不画弧：圆头线帽在长度为 0 时仍会留一个色点，看着像"有值"（自查发现） */}
        {pct > 0 && (
          <circle
            cx="55"
            cy="55"
            r={R}
            fill="none"
            stroke={color}
            strokeWidth={14}
            strokeLinecap="round"
            strokeDasharray={`${len} ${C - len}`}
            transform="rotate(-90 55 55)"
          />
        )}
        <text x="55" y="60" textAnchor="middle" className="chart-ring-num">
          {pct.toFixed(pct >= 10 ? 0 : 1)}
        </text>
      </svg>
      <div className="chart-ring-label">{label}</div>
    </div>
  );
}

/** 今日流量对比：借出 vs 归还（两根大柱，按两者最大值归一）。 */
function TodayFlow({ borrowed, returned }: { borrowed: number; returned: number }) {
  const max = Math.max(borrowed, returned, 1);
  const bars = [
    { label: "今日借出", value: borrowed, color: "var(--paint-primary)" },
    { label: "今日归还", value: returned, color: "var(--paint-secondary)" },
  ];
  return (
    <div className="chart-flow">
      {bars.map((b) => (
        <div key={b.label} className="chart-flow-col">
          <div className="chart-flow-track">
            <div
              className="chart-flow-fill"
              // 0 值不画柱身：留一小截"最小高度"会被读成"有一点"，是假象（自查发现）
              style={{ height: `${b.value > 0 ? Math.max(6, (b.value / max) * 100) : 0}%`, background: b.color }}
            />
          </div>
          <div className="chart-flow-num">{b.value}</div>
          <div className="chart-flow-label">{b.label}</div>
        </div>
      ))}
    </div>
  );
}

/** 近 N 天借还趋势：折线 + 面积（借出面积填充，归还虚线）。 */
function TrendLine({ points }: { points: { date: string; borrowed: number; returned: number }[] }) {
  const W = 300;
  const H = 128;
  const PAD = 18;
  if (!points.length) return <div className="chart-empty">暂无数据</div>;
  const max = Math.max(1, ...points.map((p) => Math.max(p.borrowed, p.returned)));
  const x = (i: number) => PAD + (i * (W - PAD * 2)) / Math.max(1, points.length - 1);
  const y = (v: number) => H - PAD - (v / max) * (H - PAD * 2);
  const line = (key: "borrowed" | "returned") =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ");
  const area = `${line("borrowed")} L${x(points.length - 1).toFixed(1)},${H - PAD} L${PAD},${H - PAD} Z`;
  return (
    <div className="chart-trend">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="借还趋势">
        <defs>
          <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--paint-primary)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--paint-primary)" stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#trendFill)" stroke="none" />
        <path d={line("borrowed")} fill="none" stroke="var(--paint-primary)" strokeWidth={3} />
        <path
          d={line("returned")}
          fill="none"
          stroke="var(--paint-secondary)"
          strokeWidth={2}
          strokeDasharray="6 5"
        />
        {points.map((p, i) =>
          p.borrowed > 0 ? <circle key={p.date} cx={x(i)} cy={y(p.borrowed)} r={3} fill="var(--paint-ink)" /> : null,
        )}
      </svg>
      <div className="chart-trend-axis">
        <span>{points[0]?.date}</span>
        <span>{points[Math.floor(points.length / 2)]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
      <div className="chart-trend-legend">
        <span className="chart-legend-row">
          <span className="chart-dot" style={{ background: PALETTE.borrowed }} />
          <span className="chart-legend-label">借出</span>
        </span>
        <span className="chart-legend-row">
          <span className="chart-dot" style={{ background: PALETTE.returned }} />
          <span className="chart-legend-label">归还</span>
        </span>
      </div>
    </div>
  );
}

/** 热门书 TOP N：横向条形（书名 + 次数）。 */
function HotBooks({ items }: { items: { book_id: number; title: string; borrow_count: number }[] }) {
  if (!items.length) return <div className="chart-empty">近 30 天暂无借阅</div>;
  const max = Math.max(1, ...items.map((i) => i.borrow_count));
  return (
    <div className="chart-hot">
      {items.map((it, idx) => (
        <div key={it.book_id} className="chart-hot-row">
          <span className="chart-hot-rank">{idx + 1}</span>
          <span className="chart-hot-title" title={it.title}>
            {it.title}
          </span>
          <span className="chart-hot-track">
            <span className="chart-hot-fill" style={{ width: `${(it.borrow_count / max) * 100}%` }} />
          </span>
          <span className="chart-hot-num">{it.borrow_count}</span>
        </div>
      ))}
    </div>
  );
}

/** 小读者构成：按会员状态堆叠横条 + 图例。 */
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
                background: MEMBER_COLOR[m.status] ?? "var(--paint-border)",
              }}
            />
          </Tooltip>
        ))}
      </div>
      <div className="chart-member-legend">
        {items.map((m) => (
          <span key={m.status} className="chart-legend-row">
            <span className="chart-dot" style={{ background: MEMBER_COLOR[m.status] ?? "var(--paint-border)" }} />
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

export default function DashboardCharts({ data }: { data: Overview }) {
  const d = data as unknown as Record<string, number>;
  const [charts, setCharts] = useState<ChartsData | null>(null);

  const load = useCallback(() => {
    // 投屏场景：图形区自己 60 秒轮询；失败保留上一帧、不弹错误框（店里电视无人交互）
    apiDashboardCharts(14, 5)
      .then(setCharts)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [load]);

  const total =
    (d.copy_available ?? 0) + (d.copy_borrowed ?? 0) + (d.copy_maintenance ?? 0) + (d.copy_lost ?? 0);
  return (
    <div className="chart-grid">
      <Card size="small" className="chart-card" title="馆藏构成">
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

      <Card size="small" className="chart-card" title="今日流量">
        <TodayFlow borrowed={d.today_borrowed ?? 0} returned={d.today_returned ?? 0} />
        <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
          当前逾期 <b style={{ color: "var(--paint-danger)" }}>{d.overdue_active ?? 0}</b> 本
        </Typography.Text>
      </Card>

      <Card size="small" className="chart-card" title="健康度">
        <Space size={4} style={{ display: "flex", justifyContent: "space-between" }}>
          <RateRing label="续借率" value={d.renew_rate ?? 0} color="var(--paint-primary)" />
          <RateRing label="测验通过率" value={d.quiz_pass_rate ?? 0} color="var(--paint-secondary)" />
          <RateRing label="退会率" value={d.withdrawal_rate ?? 0} color="var(--paint-yellow)" />
        </Space>
      </Card>

      <Card size="small" className="chart-card" title="近 14 天借还趋势">
        <TrendLine points={charts?.borrow_trend ?? []} />
      </Card>

      <Card size="small" className="chart-card" title="热门书 TOP5（近 30 天）">
        <HotBooks items={charts?.hot_books ?? []} />
      </Card>

      <Card size="small" className="chart-card" title="小读者构成">
        <MemberMix items={charts?.member_status ?? []} />
      </Card>
    </div>
  );
}
