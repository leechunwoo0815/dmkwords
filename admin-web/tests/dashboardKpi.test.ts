// admin-web/tests/dashboardKpi.test.ts — KPI 比例条与语义色判据（P2-4 防回归）
//
// 168 批口径：只有"有真实分母"的磁贴才画比例条（14 个业务磁贴里 8 个有）；
// 其余不编造同比/目标值。运行：cd admin-web && pnpm test:ui

import { test } from "node:test";
import assert from "node:assert/strict";

import { BUSINESS_CELLS, KPI_RATIO, cellValue, kpiTone, type Overview } from "../src/dashboardKpi.ts";

/** 只填测试需要的字段；其余字段由页面响应保证。 */
const ov = (patch: Record<string, number>): Overview => patch as unknown as Overview;

test("14 business cells, exactly 8 with a real ratio", () => {
  assert.equal(BUSINESS_CELLS.length, 14);
  assert.equal(Object.keys(KPI_RATIO).length, 8);
  const keys = new Set(BUSINESS_CELLS.map((c) => c.valueKey));
  for (const key of Object.keys(KPI_RATIO)) assert.ok(keys.has(key), `${key} not a business cell`);
});

test("copy_total ratio is available/total, null when total is 0", () => {
  assert.equal(KPI_RATIO.copy_total?.(ov({ copy_total: 10, copy_available: 4 })), 0.4);
  assert.equal(KPI_RATIO.copy_total?.(ov({ copy_total: 0, copy_available: 4 })), null);
});

test("copy_available_borrowed ratio is available/(available+borrowed), null when both 0", () => {
  assert.equal(KPI_RATIO.copy_available_borrowed?.(ov({ copy_available: 3, copy_borrowed: 5 })), 0.375);
  assert.equal(KPI_RATIO.copy_available_borrowed?.(ov({ copy_available: 0, copy_borrowed: 0 })), null);
});

test("overdue ratio is capped at 1", () => {
  assert.equal(KPI_RATIO.overdue_active?.(ov({ copy_borrowed: 10, overdue_active: 5 })), 0.5);
  assert.equal(KPI_RATIO.overdue_active?.(ov({ copy_borrowed: 10, overdue_active: 20 })), 1);
});

test("pending/new ratios use member_total, null when member_total is 0", () => {
  assert.equal(KPI_RATIO.pending_evaluation_count?.(ov({ member_total: 8, pending_evaluation_count: 2 })), 0.25);
  assert.equal(KPI_RATIO.pending_evaluation_count?.(ov({ member_total: 0, pending_evaluation_count: 2 })), null);
  assert.equal(KPI_RATIO.member_new_week?.(ov({ member_total: 8, member_new_week: 4 })), 0.5);
});

test("rate ratios are normalized to 0–1", () => {
  assert.equal(KPI_RATIO.renew_rate?.(ov({ renew_rate: 92.9 })), 0.929);
  assert.equal(KPI_RATIO.withdrawal_rate?.(ov({ withdrawal_rate: 11.1 })), 0.111);
  assert.equal(KPI_RATIO.quiz_pass_rate?.(ov({ quiz_pass_rate: 100 })), 1);
});

test("kpiTone marks overdue red / pending yellow only when >0", () => {
  assert.equal(kpiTone("overdue_active", ov({ overdue_active: 1 })), "danger");
  assert.equal(kpiTone("overdue_active", ov({ overdue_active: 0 })), "default");
  assert.equal(kpiTone("pending_evaluation_count", ov({ pending_evaluation_count: 1 })), "warning");
  assert.equal(kpiTone("copy_total", ov({ copy_total: 10 })), "default");
});

test("cellValue keeps composite book/copy fields and null for missing data", () => {
  assert.equal(cellValue(null, "copy_total"), null);
  assert.equal(
    cellValue(ov({ copy_available: 3, copy_borrowed: 5 }), "copy_available_borrowed"),
    "3 / 5",
  );
  assert.equal(
    cellValue(ov({ copy_maintenance: 1, copy_lost: 2 }), "copy_maint_lost"),
    "1 / 2",
  );
});
