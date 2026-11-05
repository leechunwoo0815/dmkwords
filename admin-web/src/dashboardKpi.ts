// admin-web/src/dashboardKpi.ts — KPI 磁贴与真实比例条规则（171 批从 Dashboard.tsx 抽出）
//
// 168 批口径：只有"有真实分母"的磁贴才画比例条（17 块里 8 块），其余不编造。
// 抽成纯函数便于单测（tests/dashboardKpi.test.ts）防止比例语义回归。

import type { components } from "./api/schema";

export type Overview = components["schemas"]["DashboardOverviewResponse"];

/** 经营看板格子（valueKey 对应 DashboardOverviewResponse 字段） */
export const BUSINESS_CELLS: { label: string; unit: string; valueKey: string }[] = [
  { label: "总藏书量", unit: "本", valueKey: "copy_total" },
  { label: "在馆 / 借出", unit: "本", valueKey: "copy_available_borrowed" },
  { label: "维护 / 遗失", unit: "本", valueKey: "copy_maint_lost" },
  { label: "今日借出", unit: "本", valueKey: "today_borrowed" },
  { label: "今日归还", unit: "本", valueKey: "today_returned" },
  { label: "当前逾期", unit: "本", valueKey: "overdue_active" },
  { label: "会员总数", unit: "人", valueKey: "member_total" },
  { label: "待评估", unit: "人", valueKey: "pending_evaluation_count" },
  { label: "本周新增会员", unit: "人", valueKey: "member_new_week" },
  { label: "续借率", unit: "%", valueKey: "renew_rate" },
  { label: "退会率", unit: "%", valueKey: "withdrawal_rate" },
  { label: "测验通过率", unit: "%", valueKey: "quiz_pass_rate" },
  { label: "里程碑达成", unit: "人", valueKey: "milestone_count" },
  { label: "近期活动报名", unit: "人次", valueKey: "activity_enroll_recent" },
];

export function cellValue(o: Overview | null, key: string): number | string | null {
  if (!o) return null;
  if (key === "copy_available_borrowed") return `${o.copy_available} / ${o.copy_borrowed}`;
  if (key === "copy_maint_lost") return `${o.copy_maintenance} / ${o.copy_lost}`;
  const raw = (o as unknown as Record<string, number | null | undefined>)[key] ?? null;
  return raw;
}

/** KPI 磁贴底部比例条：只用真实比例，取不到就不画（不编造同比/目标值）。 */
export const KPI_RATIO: Record<string, (o: Overview) => number | null> = {
  copy_total: (o) => ((o.copy_total ?? 0) > 0 ? (o.copy_available ?? 0) / (o.copy_total ?? 1) : null),
  copy_available_borrowed: (o) => {
    const base = (o.copy_available ?? 0) + (o.copy_borrowed ?? 0);
    return base > 0 ? (o.copy_available ?? 0) / base : null;
  },
  overdue_active: (o) =>
    (o.copy_borrowed ?? 0) > 0 ? Math.min(1, (o.overdue_active ?? 0) / (o.copy_borrowed ?? 1)) : null,
  pending_evaluation_count: (o) =>
    (o.member_total ?? 0) > 0 ? Math.min(1, (o.pending_evaluation_count ?? 0) / (o.member_total ?? 1)) : null,
  member_new_week: (o) =>
    (o.member_total ?? 0) > 0 ? Math.min(1, (o.member_new_week ?? 0) / (o.member_total ?? 1)) : null,
  renew_rate: (o) => (o.renew_rate ?? 0) / 100,
  withdrawal_rate: (o) => (o.withdrawal_rate ?? 0) / 100,
  quiz_pass_rate: (o) => (o.quiz_pass_rate ?? 0) / 100,
};

/** 需要被看见的值：逾期 >0 标红、待评估 >0 标黄（其余中性）。 */
export function kpiTone(key: string, o: Overview): "default" | "warning" | "danger" | "success" {
  if (key === "overdue_active" && (o.overdue_active ?? 0) > 0) return "danger";
  if (key === "pending_evaluation_count" && (o.pending_evaluation_count ?? 0) > 0) return "warning";
  return "default";
}
