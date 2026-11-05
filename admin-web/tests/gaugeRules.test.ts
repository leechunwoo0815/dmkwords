// admin-web/tests/gaugeRules.test.ts — P1-1 健康度阈值判据
//
// 零新依赖：Node 内置 test runner + 类型剥离（本机 Node 26；CI Node 22 需 --experimental-strip-types）。
// 运行：cd admin-web && pnpm test:gauge
//
// 覆盖四个边界（交接卡 §待开工 v169 A3）+ 副标题单一来源 + 退会率黄色带边界。

import { test } from "node:test";
import assert from "node:assert/strict";

import { EXCELLENT_SUMMARY, gaugeTone } from "../src/components/gaugeRules.ts";

test("renew 60 is success (higher-better, subtitle boundary)", () => {
  assert.equal(gaugeTone("renew", 60), "success");
});

test("quiz 80 is success (higher-better, subtitle boundary)", () => {
  assert.equal(gaugeTone("quiz", 80), "success");
});

test("withdrawal 5 is success (lower-better: excellent)", () => {
  assert.equal(gaugeTone("withdrawal", 5), "success");
});

test("withdrawal 90 is danger (lower-better: disastrous)", () => {
  assert.equal(gaugeTone("withdrawal", 90), "danger");
});

test("renew 59 is warning (below excellent, above danger line)", () => {
  assert.equal(gaugeTone("renew", 59), "warning");
});

test("withdrawal boundaries: 10 success / 20 warning / 21 danger", () => {
  assert.equal(gaugeTone("withdrawal", 10), "success");
  assert.equal(gaugeTone("withdrawal", 20), "warning");
  assert.equal(gaugeTone("withdrawal", 21), "danger");
});

test("subtitle is generated from the same rules (single source)", () => {
  assert.equal(EXCELLENT_SUMMARY, "优秀：续借≥60% · 通过≥80% · 退会≤10%");
});
