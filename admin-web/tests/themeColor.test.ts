// admin-web/tests/themeColor.test.ts — 主题派生色判据（P2-7 防回归）
//
// 运行：cd admin-web && pnpm test:ui

import { test } from "node:test";
import assert from "node:assert/strict";

import { withAlpha } from "../src/theme/color.ts";

test("withAlpha derives rgba from a 6-digit theme primary", () => {
  assert.equal(withAlpha("#3370FF", 0.12), "rgba(51, 112, 255, 0.12)");
  assert.equal(withAlpha("#FF6B35", 0.12), "rgba(255, 107, 53, 0.12)");
});

test("withAlpha is case-insensitive", () => {
  assert.equal(withAlpha("#4c8dff", 0.12), "rgba(76, 141, 255, 0.12)");
});

test("withAlpha preserves non-hex colors (no guessing)", () => {
  assert.equal(withAlpha("rgb(1, 2, 3)", 0.12), "rgb(1, 2, 3)");
  assert.equal(withAlpha("var(--color-primary)", 0.12), "var(--color-primary)");
});

test("withAlpha rejects 3-digit shorthand rather than mis-parsing", () => {
  assert.equal(withAlpha("#FFF", 0.12), "#FFF");
});

test("withAlpha keeps the requested alpha verbatim", () => {
  assert.equal(withAlpha("#000000", 0.45), "rgba(0, 0, 0, 0.45)");
});
