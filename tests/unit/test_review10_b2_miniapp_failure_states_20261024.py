# tests/unit/test_review10_b2_miniapp_failure_states_20261024.py — 第十轮 B2：小程序失败态**行为**验证
"""用 node 加载**真实页面模块** + 桩化 `utils/api`（全部接口拒绝，statusCode=0=网络失败），
调用页面真实的加载路径，断言"失败必须落到可见状态"——即 W7-01…06 六条缺陷的 RED/GREEN 判据。

为什么不能只靠静态检查器：`check_miniapp_wiring` 的 W4c 是**文本判据**（catch 里出现
`loadError` 等标记即可），能被"写了标记但没进 data/wxml"的实现骗过（W7-02 就是这么漏的：
`loadError` 是死标记，wxml 有分支但没人赋值）。这里跑真代码、读真 `data`。

为什么不用微信开发者工具：本机开发者工具**未运行且其内部代理 127.0.0.1:7890 已下线**
（CLI 原文：`tunneling socket could not be established, cause=connect ECONNREFUSED 127.0.0.1:7890`
→ `APPID_ERROR`），IDE 自动化通道本轮不可用 ⇒ 用 node 桩跑同一条失败路径（打得进 CI/门禁）。
真机/IDE 复跑步骤见 `docs/04` §六十二。

node 不可用则 skip（与 `test_miniapp_platform_isios` 同款）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MINIAPP = ROOT / "miniapp"

HARNESS = r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const MINIAPP = __MINIAPP__;

// 桩：所有 utils/api 调用都 reject（网络失败语义，statusCode=0）
function rejectApi() {
  const err = new Error('模拟网络故障');
  err.statusCode = 0;
  return Promise.reject(err);
}
function stubApi() {
  return new Proxy({}, { get: () => (..._a) => rejectApi() });
}
function okApi(overrides) {
  return new Proxy({}, { get: (_t, name) => async (...a) => {
    if (overrides && overrides[name]) return overrides[name](...a);
    return {};
  } });
}

function loadPage(rel, opts) {
  const file = path.join(MINIAPP, rel);
  const src = fs.readFileSync(file, 'utf8');
  let captured = null;
  const sandboxRequire = (spec) => {
    if (spec.endsWith('utils/api')) return opts.api || stubApi();
    if (spec.endsWith('utils/session')) return { resolveChildId: (o) => Number((o && o.child_id) || 1) };
    if (spec.endsWith('utils/configs')) {
      return { loadConfigs: async () => ({ configs: { quiz_questions_per_book: 5 } }),
               getConfigs: () => ({ quiz_questions_per_book: 5 }) };
    }
    if (spec.endsWith('utils/media')) return { fullUrl: (u) => u || '', mediaUrl: (u) => u || '' };
    if (spec.endsWith('utils/img')) return { onImgError: function () {} };
    if (spec.endsWith('utils/format')) return new Proxy({}, { get: () => (v) => String(v) });
    // 其它 utils 模块照常走真实实现（避免桩与真实行为漂移）
    const real = path.resolve(path.dirname(file), spec);
    return require(real);
  };
  const wx = {
    getStorageSync: () => opts.storageValue === undefined ? '' : opts.storageValue,
    setStorageSync: () => {},
    showToast: (...a) => { (opts.toasts || (opts.toasts = [])).push(a[0]); },
    navigateBack: () => { opts.wentBack = true; },
    redirectTo: () => {},
    switchTab: () => {},
    stopPullDownRefresh: () => {},
  };
  const sandbox = {
    Page: (cfg) => { captured = cfg; },
    Component: () => {},
    wx,
    getApp: () => ({ globalData: { baseURL: 'http://127.0.0.1:9' } }),
    require: sandboxRequire,
    console,
    setTimeout,
    clearTimeout,
    Math, Date, JSON, Number, String, Object, Array, Promise, Error, RegExp,
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: rel });
  if (!captured) throw new Error('Page() 未被调用: ' + rel);
  const inst = Object.assign({}, captured);
  inst.data = JSON.parse(JSON.stringify(captured.data || {}));
  inst.setData = function (patch) { Object.assign(this.data, patch); };
  inst._opts = opts;
  return inst;
}

async function main() {
  const pages = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const out = [];
  for (const spec of pages) {
    const opts = { toasts: [] };
    const inst = loadPage(spec.file, opts);
    try {
      if (inst.onLoad) await inst.onLoad(spec.options || {});
      await new Promise((r) => setTimeout(r, 30));   // onShow 里的 load() 未 await：等一拍再读
      if (inst.onShow) await inst.onShow();
      await new Promise((r) => setTimeout(r, 30));
      if (inst.load && spec.callLoad) await inst.load(spec.loadArg);
      const keys = spec.expectFields || [];
      const got = {};
      for (const k of keys) got[k] = inst.data[k];
      out.push({ id: spec.id, ok: true, fields: got, toasts: opts.toasts, wentBack: !!opts.wentBack });
    } catch (e) {
      out.push({ id: spec.id, ok: false, error: String(e && e.message) });
    }
  }
  // 7) reader 快进：桩一个音频对象，断言 seek 被调到 currentTime+15（并夹到 duration）
  try {
    const r = loadPage('pages/reading-pkg/reader/reader.js', { toasts: [] });
    const calls = [];
    r._audio = { currentTime: 30, duration: 100, seek: (t) => calls.push(t) };
    r.data.duration = 100;
    r._reportNow = () => {};
    r.onSkipForward();
    r._audio = { currentTime: 95, duration: 100, seek: (t) => calls.push(t) };
    r.onSkipForward();
    r._audio = null;
    r.onSkipForward(); // 无音频对象时不许抛
    out.push({ id: 'reader-forward', ok: true, fields: { seeks: calls } });
  } catch (e) {
    out.push({ id: 'reader-forward', ok: false, error: String(e && e.message) });
  }
  // 8) 恢复腿（W7-01）：接口恢复后重试必须清掉失败态
  try {
    const good = { messages: () => ({ items: [], total: 0, unread: 0 }) };
    const inst = loadPage('pages/order-pkg/messages/messages.js', { api: okApi(good), toasts: [] });
    await inst.load(true);
    out.push({ id: 'messages-recovery', ok: true, fields: { error: inst.data.error, listError: inst.data.listError } });
  } catch (e) {
    out.push({ id: 'messages-recovery', ok: false, error: String(e && e.message) });
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { console.error(e); process.exit(1); });
"""

SPECS = [
    {
        "id": "messages",
        "file": "pages/order-pkg/messages/messages.js",
        "options": {"child_id": "1"},
        "expectFields": ["error", "listError"],
    },
    {
        "id": "profile-card",
        "file": "pages/member-pkg/profile-card/profile-card.js",
        "options": {"child_id": "1"},
        "expectFields": ["loadError", "noChild"],
    },
    {
        "id": "achievement",
        "file": "pages/member-pkg/achievement/achievement.js",
        "options": {"child_id": "1", "child_name": "%E6%BC%94%E7%A4%BA%E5%AD%A9"},
        "expectFields": ["loadError", "points"],
    },
    {
        "id": "quiz",
        "file": "pages/reading-pkg/quiz/quiz.js",
        "options": {"book_id": "30", "child_id": "1"},
        "expectFields": ["loadError", "status", "questions"],
    },
    {
        "id": "quiz-result",
        "file": "pages/reading-pkg/quiz-result/quiz-result.js",
        "options": {"book_id": "30", "child_id": "1"},
        "expectFields": ["loadError", "result"],
    },
    {
        "id": "share",
        "file": "pages/circle/share.js",
        "options": {"child_id": "1"},
        "expectFields": ["loadError", "available", "shared", "loading"],
    },
]


def _run(specs: list[dict]) -> list[dict]:
    if not shutil.which("node"):
        pytest.skip("node 不可用（CI 装 node，见 ci.yml）")
    harness = ROOT / ".zcode-tmp-b2-harness.js"  # 落仓根：node 需要能 require 真实 utils 模块
    spec_file = ROOT / ".zcode-tmp-b2-specs.json"
    try:
        harness.write_text(
            HARNESS.replace("__MINIAPP__", json.dumps(str(MINIAPP))), encoding="utf-8"
        )
        spec_file.write_text(json.dumps(specs), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(harness), str(spec_file)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        harness.unlink(missing_ok=True)
        spec_file.unlink(missing_ok=True)
    assert proc.returncode == 0, f"node harness 失败：{proc.stderr[-800:]}"
    return json.loads(proc.stdout)


def test_pages_land_on_visible_failure_state():
    """六个页面在"接口全挂"时都必须把**可见失败态写进 data**（RED=静默 catch/判据取反）。

    逐页期望：
      · messages   → `error=true`（首屏失败整页态；修前是 `error=!reset` → 渲染成「暂无消息」）
      · profile-card → `loadError` 非空（修前从不赋值 → 除导航栏外整页空白）
      · achievement → `loadError` 非空（修前静默 → 「还没有积分记录」）
      · quiz       → `loadError` 非空 + status/questions 清空（修前渲染「第 1 题」空题面）
      · quiz-result → `loadError` 非空（修前 toast「没有测验记录」+ 自动返回）
      · share      → `loadError` 非空（修前「还没有可晒的新成就」）
    """
    results = {r["id"]: r for r in _run(SPECS)}
    for spec in SPECS:
        r = results[spec["id"]]
        assert r["ok"], f"{spec['id']} 加载路径抛异常：{r.get('error')}"
        f = r["fields"]
        if spec["id"] == "messages":
            assert f["error"] is True, f"messages 首屏失败必须是整页失败态，实际 {f}"
        else:
            assert f.get("loadError"), f"{spec['id']} 失败没落到可见失败态：{f}"
        if spec["id"] == "quiz":
            assert f["status"] == "" and f["questions"] == [], f"quiz 失败必须清空题面：{f}"
        if spec["id"] == "achievement":
            assert f["points"] == [], f"achievement 失败不该留着假积分：{f}"
        if spec["id"] == "share":
            assert f["loading"] is False, f"share 失败后不许停在骨架屏：{f}"


def test_reader_skip_forward_seeks_15s_and_clamps():
    """W7-07：右侧「15s ↻」的 handler 必须真的 seek（+15s，末尾夹到 duration；无音频不抛）。

    RED：把 wxml 的 `bindtap="onSkipForward"` 去掉 ⇒ `check_miniapp_bindings` 判红（W1）；
    把本 handler 删掉 ⇒ 本用例红（找不到方法/不 seek）。
    """
    results = {r["id"]: r for r in _run(SPECS)}
    assert results["messages"]["ok"], results
    r = results["reader-forward"]
    assert r["id"] == "reader-forward" and r["ok"], r
    assert r["fields"]["seeks"] == [45, 100], (
        f"快进必须是 currentTime+15 且夹到 duration：{r['fields']}"
    )


def test_failure_state_clears_after_recovery():
    """恢复腿：接口恢复后重试必须清掉失败态（不是"永久横幅"）。"""
    r = {x["id"]: x for x in _run(SPECS)}["messages-recovery"]
    assert r["ok"], r
    assert r["fields"]["error"] is False and r["fields"]["listError"] == "", r["fields"]


def test_scan_pages_are_real_files():
    """W8-D4(a) 的判据面守卫（B2 扩面时同步落地）：W4c 清单里每个路径必须真实存在。

    防的是"清单条目被改名/移动后静默失效 = 覆盖面悄悄缩小"（探针实测：传不存在路径返回 []）。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cmw_wiring", ROOT / "scripts/check_miniapp_wiring.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pages = sorted(mod.FAILURE_STATE_PAGES)
    assert len(pages) >= 12, f"W4c 覆盖页数异常少（{len(pages)}）——判据面塌缩？"
    missing = [p for p in pages if not (ROOT / p).exists()]
    assert missing == [], f"W4c 清单引用了不存在的路径（覆盖面静默缩小）：{missing}"
    # 扩面必须包含第十轮 W7 实测的六个页面（防"改了名单个删除"）
    for must in (
        "miniapp/pages/order-pkg/messages/messages.js",
        "miniapp/pages/member-pkg/profile-card/profile-card.js",
        "miniapp/pages/member-pkg/achievement/achievement.js",
        "miniapp/pages/reading-pkg/quiz/quiz.js",
        "miniapp/pages/reading-pkg/quiz-result/quiz-result.js",
        "miniapp/pages/circle/share.js",
    ):
        assert must in pages, f"W7 实测页面不在 W4c 覆盖面内：{must}"
