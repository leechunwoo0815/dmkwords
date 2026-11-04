import type { ReactNode } from "react";

/**
 * KPI 磁贴（第一百六十四批·深度改版）
 *
 * 为什么要有它：原来的仪表盘下半部分是「14 张一模一样的卡片，每张一个数字」——
 * 信息密度低、层级扁平，观感像作业。改成带图标、单位、说明、比例尺的磁贴后：
 * · 一眼能看出"这个数是大是小"（底部比例条，真实比例，不编造）
 * · 危险/警示值有颜色语义（逾期 >0 变红）
 * · 数字统一 tabular-nums，列对齐
 *
 * 全部走主题令牌：企业风是浅底细线，绘本风自动变成粗描边 + 硬阴影（见 skins/paint.css）。
 */
export interface KpiTileProps {
  icon: ReactNode;
  label: string;
  value: number | string | null;
  unit?: string;
  /** 副说明：真实数据，如「在馆 65 · 借出 3」 */
  hint?: string;
  /** 0–1 的真实比例；不传则不画比例条 */
  ratio?: number | null;
  tone?: "default" | "warning" | "danger" | "success";
}

const TONE_COLOR: Record<NonNullable<KpiTileProps["tone"]>, string> = {
  default: "var(--color-primary)",
  warning: "var(--color-warning)",
  danger: "var(--color-danger)",
  success: "var(--color-success)",
};

export default function KpiTile({
  icon,
  label,
  value,
  unit,
  hint,
  ratio,
  tone = "default",
}: KpiTileProps) {
  const color = TONE_COLOR[tone];
  const pct = ratio == null ? null : Math.max(0, Math.min(1, ratio));
  return (
    <div className="kpi-tile">
      <div className="kpi-tile-head">
        <span className="kpi-tile-icon" style={{ color, background: "var(--color-bg-subtle)" }}>
          {icon}
        </span>
        <span className="kpi-tile-label">{label}</span>
      </div>
      <div className="kpi-tile-value">
        {value ?? "—"}
        {unit && <span className="kpi-tile-unit">{unit}</span>}
      </div>
      {hint && <div className="kpi-tile-hint">{hint}</div>}
      {pct != null && (
        <div className="kpi-meter" aria-hidden>
          <span style={{ width: `${pct * 100}%`, background: color }} />
        </div>
      )}
    </div>
  );
}
