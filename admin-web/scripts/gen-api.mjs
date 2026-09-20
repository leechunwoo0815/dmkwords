#!/usr/bin/env node
// admin-web/scripts/gen-api.mjs — 前端 API 类型生成 / 校验（fix44 R2，Q10 治根）
//
// 契约源单一化：docs/api/openapi.json（T27 快照）。
//   为什么不再直连 http://localhost:8002：① CI 里没有服务，检查无法进 gate；
//   ② 本地检查会依赖"当时起着的那个后端"，读到脏/旧服务的契约而不自知。
//   快照本身由 gate[8] `python scripts/export_openapi.py --check` 保证与代码一致
//   → 链路：代码 →（gate[8]）→ 快照 →（gate[8]）→ src/api/schema.d.ts。
//
// 用法：
//   pnpm gen:api          # 由快照重新生成 src/api/schema.d.ts
//   pnpm gen:api --check  # 生成到临时文件与提交版**逐字节**比对，不一致 exit 1（gate[8] 调用）
//
// 改后端契约的顺序：先 `python scripts/export_openapi.py`（更新快照，单独 commit，
// message 带 contract-change: 前缀）→ 再 `pnpm gen:api` → 最后适配调用点 + tsc。

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url)); // admin-web/scripts
const ADMIN = resolve(HERE, ".."); // admin-web
const ROOT = resolve(ADMIN, ".."); // 仓库根
const SNAPSHOT = join(ROOT, "docs", "api", "openapi.json");
const TARGET = join(ADMIN, "src", "api", "schema.d.ts");
const BIN = join(ADMIN, "node_modules", ".bin", "openapi-typescript");
const CHECK = process.argv.includes("--check");

function generate(input, output) {
  const r = spawnSync(BIN, [input, "-o", output], { cwd: ADMIN, stdio: "inherit" });
  if (r.status !== 0) {
    console.error(`✗ openapi-typescript 生成失败（exit ${r.status}）：${input}`);
    process.exit(1);
  }
}

/** 差异摘要：首处不同行 + 行集合差（顺序无关——快照 sort_keys 与路由注册序不同，属已知噪声）。 */
function reportDiff(expected, actual) {
  const a = expected.split("\n");
  const b = actual.split("\n");
  let first = 0;
  while (first < a.length && first < b.length && a[first] === b[first]) first += 1;
  console.error(`\n✗ src/api/schema.d.ts 与契约快照不一致（首处不同：第 ${first + 1} 行）`);
  console.error(`    快照生成: ${JSON.stringify((a[first] ?? "").trim())}`);
  console.error(`    提交版本: ${JSON.stringify((b[first] ?? "").trim())}`);
  const multiset = (lines) => {
    const m = new Map();
    for (const l of lines) m.set(l, (m.get(l) ?? 0) + 1);
    return m;
  };
  const [ma, mb] = [multiset(a), multiset(b)];
  const onlySnapshot = [...ma].filter(([l, n]) => n > (mb.get(l) ?? 0));
  const onlyCommitted = [...mb].filter(([l, n]) => n > (ma.get(l) ?? 0));
  console.error(`    快照独有行 ${onlySnapshot.length} / 提交版独有行 ${onlyCommitted.length}（仅顺序差异不算漂移）`);
  for (const [l, n] of onlySnapshot.slice(0, 8)) {
    console.error(`      + ${n - (mb.get(l) ?? 0)}x ${l.trim().slice(0, 100)}`);
  }
  for (const [l, n] of onlyCommitted.slice(0, 8)) {
    console.error(`      - ${n - (ma.get(l) ?? 0)}x ${l.trim().slice(0, 100)}`);
  }
  console.error("\n修复：cd admin-web && pnpm gen:api（若后端契约也变了，先 python scripts/export_openapi.py）");
}

function main() {
  if (!existsSync(SNAPSHOT)) {
    console.error(`✗ 契约快照缺失: ${SNAPSHOT}（先跑 python scripts/export_openapi.py）`);
    process.exit(1);
  }
  if (!existsSync(BIN)) {
    console.error(`✗ 未找到 openapi-typescript: ${BIN}（先 cd admin-web && pnpm install）`);
    process.exit(1);
  }
  if (!CHECK) {
    generate(SNAPSHOT, TARGET);
    console.log(`✓ 已由契约快照生成 ${TARGET}`);
    return;
  }
  if (!existsSync(TARGET)) {
    console.error(`✗ 前端类型未生成: ${TARGET}（跑 pnpm gen:api）`);
    process.exit(1);
  }
  const dir = mkdtempSync(join(tmpdir(), "gen-api-check-"));
  const tmp = join(dir, "schema.d.ts");
  try {
    generate(SNAPSHOT, tmp);
    const expected = readFileSync(tmp, "utf8");
    const actual = readFileSync(TARGET, "utf8");
    if (expected === actual) {
      console.log("✓ 前端类型与契约快照逐字节一致（schema.d.ts 无需更新）");
      return;
    }
    reportDiff(expected, actual);
    process.exit(1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

main();
