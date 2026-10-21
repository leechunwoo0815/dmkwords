# tests/unit/test_audit8_c3_checker_rules_20261021.py — 第八轮 C 批残留（P2-67/69/70）
"""**缺陷（第八轮审查 §7 C 批残留，逐条实测✓）**：

  · **P2-67** 反假绿检查器 `check_fake_assertions` **规则窄**：`assert True, "x"`（带消息）、
    恒真比较（`assert x == x` / `assert 1 == 1`）、**构造式**时间炸弹（`datetime(2026, 12, 1)`）、
    `.feature` 文件——四类一处都不拦（探针实测 violations=[] bombs=[]）。
  · **P2-69** BDD `@draft/@wip/@future` **可任意加、门禁无基线守卫**：一行标签即可把场景
    静默摘出门禁（`behave.ini` 的 `~@draft` 就是开关，没人看着）。
  · **P2-70** 小程序接线/数据面检查**假阴性**：`bind:` 冒号式（仓库里 17 处，含自定义组件
    驼峰事件名）与单引号绑定整类漏检；`custom-tab-bar/` 不在扫描面；数据面"写入过 data"
    判据**过宽**（任意 `key:` 都算 → 无关对象字面量的键也能让断链"过关"）。

本文件把**修复后的判据**钉成可证伪断言：每条规则既有"违例必须命中"，也有
"合法写法不得误报"的对照组（红线 171 族：只断言命中会被"一律报错"式过修骗过）。
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_probe_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan_fake(tmp_path: pathlib.Path, body: str, *, feature: str | None = None):
    """把样本写成 probe 文件 → 跑 check_fake_assertions.scan（now_date 固定，避免时间漂移）。"""
    mod = _load("check_fake_assertions")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "features/steps").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests/probe.py").write_text(body, encoding="utf-8")
    if feature is not None:
        (tmp_path / "features/probe.feature").write_text(feature, encoding="utf-8")
    return mod.scan(tmp_path, now_date=datetime.date(2026, 10, 21))


# ------------------------------------------------------------------ P2-67 规则 A（假绿断言）


def test_constant_assert_with_message_is_caught(tmp_path):
    """A1：`assert True, "看起来在断言"` 必须被拦——消息字符串不是"说明原因"。

    RED（还原修复前）：原实现见 `"` 就放行 → 本用例红。
    """
    violations, _ = _scan_fake(tmp_path, 'assert True, "看起来在断言"\n')
    assert len(violations) == 1 and "常量断言" in violations[0], violations


def test_constant_assert_with_comment_is_allowed(tmp_path):
    """A1 逃生口：同行注释说明原因（behave 无 pytest.skip 的前端交互步骤）→ 零违例。"""
    violations, _ = _scan_fake(tmp_path, "assert True  # 前端交互步骤无服务端断言\n")
    assert violations == [], violations


def test_tautological_comparisons_are_caught(tmp_path):
    """A2：两侧同一表达式 / 双常量比较必须被拦（与代码行为无关的断言）。"""
    violations, _ = _scan_fake(tmp_path, "assert x == x\nassert 1 == 1\nassert 'a' == 'a'\n")
    assert len(violations) == 3, violations
    assert any("恒真比较" in v for v in violations), violations
    assert any("双常量比较" in v for v in violations), violations


def test_side_effecting_comparison_is_not_flagged(tmp_path):
    """A2 对照组：`assert f() == f()` 有副作用，**不得**判恒真（过修防线）。"""
    violations, _ = _scan_fake(tmp_path, "assert f() == f()\nassert r.status_code == 404\n")
    assert violations == [], violations


# ------------------------------------------------------------------ P2-67 规则 B（时间炸弹）


def test_datetime_constructor_bomb_in_payload_key_is_caught(tmp_path):
    """B2：`{"start_at": datetime(2026, 12, 1, 10, 0)}` 必须被拦（构造式，修复前一处不拦）。"""
    _, bombs = _scan_fake(tmp_path, '{"start_at": datetime(2026, 12, 1, 10, 0)}\n')
    assert len(bombs) == 1 and "构造式" in bombs[0], bombs


def test_past_and_sentinel_dates_are_not_bombs(tmp_path):
    """B2 对照组：历史账单日 / 纯函数 now 注入 / 远期哨兵 / 相对时间 → 零炸弹。

    这三条正是修复过程中**真实出现过**的误报形状（探针实测 21 处：`bill_date=date(2026, 10, 1)`、
    `cron_due_today(..., datetime(2026, 9, 24, 9, 44))`）；写死一个**已过去**的日期不会"到点失效"。
    """
    _, bombs = _scan_fake(
        tmp_path,
        "bill_date=date(2026, 10, 1)\n"
        'cron_due_today("0 8 * * *", now=datetime(2026, 12, 1, 9, 44))\n'
        '{"start_at": "2026-01-01T10:00:00"}\n'
        '{"start_at": "2099-01-01T10:00:00"}\n'
        '{"deadline": datetime.now() + timedelta(days=7)}\n',
    )
    assert bombs == [], bombs


def test_feature_file_time_bomb_is_caught(tmp_path):
    """B3：`.feature` 场景表格里的 `start_at 2026-12-01T…` 必须被拦（修复前整类不在扫描面）。"""
    _, bombs = _scan_fake(
        tmp_path,
        "x = 1\n",
        feature="# language: zh-CN\n功能: f\n  场景: s\n    假如 开始 start_at 2026-12-01T10:00:00\n",
    )
    assert len(bombs) == 1 and "probe.feature" in bombs[0], bombs


# ------------------------------------------------------------------ P2-69 BDD 挂起基线


def test_repo_bdd_baseline_is_consistent():
    """真实仓库：挂起集合与基线一致、活动面数字一致、每条挂起都有理由。

    RED（还原修复前）：没有基线文件 → 检查器判"基线缺失"；或有人给场景挂 @draft 而不登记 → 本用例红。
    """
    mod = _load("check_bdd_baseline")
    baseline = mod.load_baseline(ROOT)
    assert baseline, "features/bdd_baseline.json 缺失——挂起集合没有事实源"
    assert mod.find_problems(ROOT, baseline) == [], mod.find_problems(ROOT, baseline)
    for rel, entry in baseline["draft_files"].items():
        assert entry["reason"].strip(), f"{rel} 的挂起没写理由"
    for entry in baseline["draft_scenarios"]:
        assert entry["reason"].strip(), f"{entry['file']} 「{entry['name']}」的挂起没写理由"


def test_bdd_baseline_catches_silently_added_draft(tmp_path):
    """守卫①：给活动场景挂一行 `@draft`（基线未登记）必须判红。

    RED（还原修复前）：无基线守卫 → 一行标签把场景摘出门禁，门禁全绿。
    """
    mod = _load("check_bdd_baseline")
    (tmp_path / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "behave.ini").write_text(
        "[behave]\ntags = ~@wip\n    ~@future\n    ~@draft\n", encoding="utf-8"
    )
    feat = tmp_path / "features/a.feature"
    feat.write_text(
        "# language: zh-CN\n功能: f\n  场景: 会跑的场景\n    假如 一步\n", encoding="utf-8"
    )
    mod.write_baseline(tmp_path)
    baseline = mod.load_baseline(tmp_path)
    assert mod.find_problems(tmp_path, baseline) == [], mod.find_problems(tmp_path, baseline)
    feat.write_text(
        "# language: zh-CN\n功能: f\n  @draft\n  场景: 偷偷挂起\n    假如 一步\n", encoding="utf-8"
    )
    problems = mod.find_problems(tmp_path, baseline)
    assert any("新增挂起" in p for p in problems), problems


def test_bdd_baseline_catches_deleted_scenario(tmp_path):
    """守卫③：删场景让门禁变绿（活动面下降）必须判红。"""
    mod = _load("check_bdd_baseline")
    (tmp_path / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "behave.ini").write_text(
        "[behave]\ntags = ~@wip\n    ~@future\n    ~@draft\n", encoding="utf-8"
    )
    feat = tmp_path / "features/a.feature"
    feat.write_text(
        "# language: zh-CN\n功能: f\n  场景: 一\n    假如 一步\n  场景: 二\n    假如 一步\n",
        encoding="utf-8",
    )
    mod.write_baseline(tmp_path)
    baseline = mod.load_baseline(tmp_path)
    assert baseline["active"]["scenarios"] == 2, baseline
    feat.write_text("# language: zh-CN\n功能: f\n  场景: 一\n    假如 一步\n", encoding="utf-8")
    problems = mod.find_problems(tmp_path, baseline)
    assert any("活动面" in p and "scenarios" in p for p in problems), problems


def test_bdd_baseline_requires_behave_ini_exclusions(tmp_path):
    """守卫④：`behave.ini` 不再排除 `~@draft` 时判红（挂起集合照旧"绿"但实际会跑的盲区）。"""
    mod = _load("check_bdd_baseline")
    (tmp_path / "features").mkdir(parents=True, exist_ok=True)
    (tmp_path / "behave.ini").write_text(
        "[behave]\ntags = ~@wip\n    ~@future\n    ~@draft\n", encoding="utf-8"
    )
    (tmp_path / "features/a.feature").write_text(
        "# language: zh-CN\n功能: f\n  场景: 一\n    假如 一步\n", encoding="utf-8"
    )
    mod.write_baseline(tmp_path)
    baseline = mod.load_baseline(tmp_path)
    (tmp_path / "behave.ini").write_text("[behave]\ntags = ~@wip\n", encoding="utf-8")
    problems = mod.find_problems(tmp_path, baseline)
    assert any("不再排除" in p for p in problems), problems


# ------------------------------------------------------------------ P2-70 小程序接线 / 数据面


def test_wiring_detects_colon_form_and_single_quote_bindings(tmp_path):
    """W1：`bind:onRetry="onMissing"`（冒号式，自定义组件驼峰事件）与单引号绑定必须命中。

    RED（还原修复前：`bind` + `[a-z]+` + 双引号）：两类整类漏检 → 本用例红。
    """
    mod = _load("check_miniapp_wiring")
    wxml = tmp_path / "colon.wxml"
    wxml.write_text(
        '<error-view bind:onRetry="onMissingColon" />\n'
        "<view bind:action='onMissingSingle'>x</view>\n"
        '<view catch:tap="onMissingCatch">y</view>\n',
        encoding="utf-8",
    )
    (tmp_path / "colon.js").write_text("Page({\n  onLoad() {},\n})\n", encoding="utf-8")
    (tmp_path / "app.wxss").write_text(".x {}\n", encoding="utf-8")
    hits = [p for p in mod.scan([wxml], app_wxss=tmp_path / "app.wxss") if p.startswith("W1")]
    for want in ("onMissingColon", "onMissingSingle", "onMissingCatch"):
        assert any(want in h for h in hits), f"{want} 未命中：{hits}"


def test_wiring_scans_custom_tab_bar():
    """扫描面：`custom-tab-bar/` 必须在接线检查的扫描目录里（自定义 tabBar 独立目录）。

    RED（还原修复前）：扫描面只有 pages/components → custom-tab-bar 的 handler 与
    `{{list}}/{{selected}}/{{circleDot}}` 全在盲区。
    """
    mod = _load("check_miniapp_wiring")
    dirs = {p.name for p in mod.SCAN_DIRS}
    assert "custom-tab-bar" in dirs, dirs
    tab_wxml = ROOT / "miniapp/custom-tab-bar/index.wxml"
    assert tab_wxml in [p for d in mod.SCAN_DIRS for p in d.rglob("*.wxml")], (
        "tabBar wxml 未进扫描面"
    )


def test_bindings_ignores_unrelated_object_literal_keys():
    """数据面：无关对象字面量里的键**不算**写入过 data（修复前过宽判据的假阴性形状）。

    RED（还原修复前：全文件 `(\\w+)\\s*:` 全匹配）：`ghostVar` 被判成"有 data 来源" → 本用例红。
    """
    mod = _load("check_miniapp_bindings")
    js = "function f(res) {\n  return res.map((row) => ({ ghostVar: row.n }))\n}\nPage({ data: {} })\n"
    assert mod.missing_for("<view>{{ ghostVar }}</view>\n", js) == ["ghostVar"]
    # 请求载荷里的 data 也不是页面 data
    js2 = "wx.request({ url: '/x', data: { childId: 1 } })\nPage({ data: {} })\n"
    assert mod.missing_for("<view>{{ childId }}</view>\n", js2) == ["childId"]


def test_bindings_counts_setdata_and_component_properties():
    """对照组：`setData({...})` 参数与组件 `properties:` 键**必须**算写入（不得收窄过头）。"""
    mod = _load("check_miniapp_bindings")
    js = "Page({ data: {} })\n  this.setData({ loadError: e.message })\n"
    assert mod.missing_for("<view>{{ loadError }}</view>\n", js) == []
    comp = (
        "Component({\n  properties: {\n    visible: { type: Boolean },\n"
        "    retryText: { type: String },\n  },\n  data: {},\n})\n"
    )
    assert mod.missing_for("<view>{{ visible }}{{ retryText }}</view>\n", comp) == []
