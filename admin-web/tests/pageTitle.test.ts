// admin-web/tests/pageTitle.test.ts — 动态页面标题判据（P3 #9 防回归）
//
// 运行：cd admin-web && pnpm test:ui

import { test } from "node:test";
import assert from "node:assert/strict";

import { PAGE_TITLES, pageTitleFor } from "../src/pageTitles.ts";

test("/books/:id resolves to 图书详情", () => {
  assert.equal(pageTitleFor("/books/1"), "图书详情");
  assert.equal(pageTitleFor("/books/123/"), "图书详情");
});

test("/books resolves to 图书管理", () => {
  assert.equal(pageTitleFor("/books"), "图书管理");
});

test("static route resolves from PAGE_TITLES", () => {
  assert.equal(pageTitleFor("/configs"), "系统配置");
  assert.equal(pageTitleFor("/"), "仪表盘");
});

test("unknown route returns null (caller falls back to brand)", () => {
  assert.equal(pageTitleFor("/nope"), null);
  assert.equal(pageTitleFor("/bookshelf"), null);
});

test("PAGE_TITLES has the 15 layout routes", () => {
  assert.equal(Object.keys(PAGE_TITLES).length, 15);
});
