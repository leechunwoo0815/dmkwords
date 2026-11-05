// admin-web/src/components/gaugeRules.ts — 健康度仪表阈值（唯一事实源）
//
// 2026-11-05 第一百六十五批：修 P1-1「健康度阈值语义反向」。
// 原实现三个仪表共用「≥80 绿 / ≥50 黄 / 其余红」，把越低越好的「退会率」也按越高越绿着色
// （退会 5% 标红、90% 标绿），且与卡片副标题「续借≥60 · 通过≥80 · 退会≤10」矛盾。
// 现按指标方向抽成规则表：仪表取色与副标题都从这里派生，禁止两处各写一套阈值。
// 口径见 docs/04 S445/S453、docs/31 §8.7 B。

export type GaugeMetric = "renew" | "quiz" | "withdrawal";
export type GaugeTone = "success" | "warning" | "danger";

type GaugeRule = {
  label: string;
  /** higher-better：越高越好（续借/通过）；lower-better：越低越好（退会） */
  direction: "higher-better" | "lower-better";
  /** 达到该值即「优秀」（higher-better 用 >=，lower-better 用 <=） */
  success: number;
  /** 未达优秀但达到该值即「警示」（higher-better 用 >=，lower-better 用 <=） */
  warning: number;
  /** 副标题文案，形如「续借≥60%」 */
  excellent: string;
};

export const GAUGE_RULES = {
  renew: { label: "续借率", direction: "higher-better", success: 60, warning: 50, excellent: "续借≥60%" },
  quiz: { label: "测验通过率", direction: "higher-better", success: 80, warning: 50, excellent: "通过≥80%" },
  withdrawal: { label: "退会率", direction: "lower-better", success: 10, warning: 20, excellent: "退会≤10%" },
} satisfies Record<GaugeMetric, GaugeRule>;

/** 卡片副标题：优秀阈值与 GAUGE_RULES 同源，避免文案和取色再次漂移 */
export const EXCELLENT_SUMMARY = `优秀：${Object.values(GAUGE_RULES)
  .map((rule) => rule.excellent)
  .join(" · ")}`;

/** 按指标方向取色档；非有限数按 0 处理（与仪表盘的 pct 钳制一致） */
export function gaugeTone(metric: GaugeMetric, value: number): GaugeTone {
  const pct = Number.isFinite(value) ? value : 0;
  const rule = GAUGE_RULES[metric];
  if (rule.direction === "higher-better") {
    return pct >= rule.success ? "success" : pct >= rule.warning ? "warning" : "danger";
  }
  return pct <= rule.success ? "success" : pct <= rule.warning ? "warning" : "danger";
}
