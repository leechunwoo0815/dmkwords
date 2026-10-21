#!/usr/bin/env python
"""BDD 挂起基线守卫（2026-10-21 第八轮审查 P2-69）。

**要防什么**：`behave.ini` 里 `tags = ~@wip ~@future ~@draft` 决定了哪些场景进 gate。
于是"给场景挂一行 `@draft`"就能把它从门禁里静默摘掉——改坏一个场景、挂个标签、
门禁照样全绿，**没有任何机制知道少测了什么**（P2-69 实测：全仓无 @draft 基线文件、
gate 无守卫、`behave.ini` 的排除行也没人看着）。

**守卫四条**（全部机械可判定）：
  ① 挂起集合**逐条一致**：基线里记着每个 `@draft/@wip/@future` 的文件与场景（含标签）。
     多一条 = 有人在静默挂起；少一条 = 有人解封了但没更新基线（解封是好事，但必须是
     显式动作——`--update` 刷新基线 + LEDGER 留痕，让这次"少测了什么"进 diff）。
  ② **每条挂起必须写明理由**（基线里的 `reason` 非空）：挂起一个场景不能是"一行标签的事"。
  ③ 活动面（features / scenarios / steps）**精确匹配基线**：删场景让门禁变绿 = 红；
     新增/改写场景同样要 `--update`（本项目既有纪律：BDD 基线变动须在 LEDGER 留痕，
     这条把它变成机器拦得住的形状）。
  ④ `behave.ini` 必须仍然排除三类标签（把 `~@draft` 删掉 = 挂起集合照旧"绿"但实际会跑，
     是本检查器的盲区形状，必须单独拦）。

**口径**：本检查器用 behave 自己的解析器（`behave.parser.parse_file`）计数，
Scenario Outline 按 Examples 行展开、背景步按场景重复计入——与 behave 实跑打印的
`N features / N scenarios / N steps` 逐字对齐（2026-10-21 实测：27/249/950 活动 +
11/96/275 挂起，与 `gate-runs/2026-10-19/gate-191822.log` 的 BDD 段完全一致）。

**基线文件**：`features/bdd_baseline.json`——改它是一次显式动作，diff 里看得见。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FEATURES = "features"
BASELINE_NAME = "bdd_baseline.json"
DRAFT_TAGS = ("draft", "wip", "future")
BEHAVE_INI = "behave.ini"


def _parse_features(features_dir: pathlib.Path) -> tuple[dict, dict, list[dict]]:
    """解析全部 `.feature` → (活动计数, 文件级挂起, 场景级挂起)。"""
    from behave.model import ScenarioOutline
    from behave.parser import parse_file

    counts = {"features": 0, "scenarios": 0, "steps": 0}
    draft_files: dict[str, dict] = {}
    draft_scenarios: list[dict] = []
    for path in sorted(features_dir.rglob("*.feature")):
        rel = path.relative_to(features_dir.parent).as_posix()
        feature = parse_file(str(path))
        file_tags = sorted({t.lstrip("@") for t in feature.tags} & set(DRAFT_TAGS))
        background_steps = len(feature.background.steps) if feature.background else 0
        active_here = 0
        for scenario in feature.scenarios:
            own_tags = sorted({t.lstrip("@") for t in scenario.tags} & set(DRAFT_TAGS))
            if isinstance(scenario, ScenarioOutline):
                rows = sum(len(ex.table.rows) for ex in scenario.examples if ex.table)
                n_scenarios = rows
                n_steps = rows * (len(scenario.steps) + background_steps)
            else:
                n_scenarios = 1
                n_steps = len(scenario.steps) + background_steps
            if file_tags:
                continue  # 整文件挂起：由文件级条目代表（场景级不再重复登记）
            if own_tags:
                draft_scenarios.append(
                    {"file": rel, "name": scenario.name, "tags": own_tags, "reason": ""}
                )
                continue
            counts["scenarios"] += n_scenarios
            counts["steps"] += n_steps
            active_here += n_scenarios
        if file_tags:
            draft_files[rel] = {"tags": file_tags, "reason": ""}
        elif active_here:
            counts["features"] += 1
    return counts, draft_files, draft_scenarios


def _behave_ini_problems(root: pathlib.Path) -> list[str]:
    """守卫 ④：`behave.ini` 必须仍然排除三类挂起标签。"""
    ini = root / BEHAVE_INI
    if not ini.is_file():
        return [f"{BEHAVE_INI} 不存在——BDD 的标签排除规则无处可查（门禁语义不明）"]
    text = ini.read_text(encoding="utf-8")
    problems = []
    for tag in DRAFT_TAGS:
        if not re.search(rf"~\s*@{tag}\b", text):
            problems.append(
                f"{BEHAVE_INI} 不再排除 `~@{tag}`——挂起场景会真的执行，"
                f"而本检查器的「活动面」口径仍把它们当挂起（盲区形状，必须判红）"
            )
    return problems


def find_problems(root: pathlib.Path, baseline: dict) -> list[str]:
    """判定只在这一处（main 与自证共用）。"""
    problems: list[str] = _behave_ini_problems(root)
    features_dir = root / FEATURES
    if not features_dir.is_dir():
        return problems + [f"{FEATURES}/ 目录不存在——BDD 基线守卫无对象"]
    counts, draft_files, draft_scenarios = _parse_features(features_dir)
    if counts["scenarios"] == 0 and not draft_files and not draft_scenarios:
        return problems + [f"{FEATURES}/ 下解析到 0 个场景——扫描面塌缩（路径/解析器失效）"]

    # 守卫 ③：活动面精确匹配
    base_active = baseline.get("active", {})
    for key in ("features", "scenarios", "steps"):
        want, got = base_active.get(key), counts[key]
        if want != got:
            problems.append(
                f"活动面 {key} 与基线不符：基线 {want} / 当前 {got}"
                "——删场景让门禁变绿是红线；确为新增或改写场景请跑 `--update` 刷新基线"
                "（并在 LEDGER 留痕）"
            )

    # 守卫 ①+②：挂起集合逐条一致 + 理由非空
    base_files = baseline.get("draft_files", {})
    for rel in sorted(set(base_files) - set(draft_files)):
        problems.append(
            f"{rel} 已不在挂起集合里（基线仍记着）——解封是好事，但必须显式："
            "跑 `--update` 刷新基线，让「少测了什么变多了」进 diff 与 LEDGER"
        )
    for rel in sorted(set(draft_files) - set(base_files)):
        problems.append(
            f"{rel} **新增挂起**（基线里没有）——一行 `@draft` 就能把整个文件摘出门禁；"
            "要么实现它，要么在基线里登记并写明理由（`--update` 后补 reason）"
        )
    for rel in sorted(set(base_files) & set(draft_files)):
        if base_files[rel].get("tags") != draft_files[rel]["tags"]:
            problems.append(
                f"{rel} 的挂起标签变了：基线 {base_files[rel].get('tags')} / "
                f"当前 {draft_files[rel]['tags']}"
            )

    def _key(entry: dict) -> tuple[str, str]:
        return entry["file"], entry["name"]

    base_scn = {_key(e): e for e in baseline.get("draft_scenarios", [])}
    now_scn = {_key(e): e for e in draft_scenarios}
    for key in sorted(set(base_scn) - set(now_scn)):
        problems.append(
            f"{key[0]} 场景「{key[1]}」已不在挂起集合里（基线仍记着）——解封须显式：`--update`"
        )
    for key in sorted(set(now_scn) - set(base_scn)):
        problems.append(
            f"{key[0]} 场景「{key[1]}」**新增挂起**（基线里没有）——"
            "一行 `@draft` 就能把场景摘出门禁；要么实现它，要么登记并写明理由"
        )
    for key in sorted(set(base_scn) & set(now_scn)):
        if base_scn[key].get("tags") != now_scn[key]["tags"]:
            problems.append(
                f"{key[0]} 场景「{key[1]}」的挂起标签变了："
                f"基线 {base_scn[key].get('tags')} / 当前 {now_scn[key]['tags']}"
            )

    # 守卫 ②：每条挂起都要有理由（文件级 + 场景级）
    for rel in sorted(draft_files):
        reason = (base_files.get(rel, {}) or {}).get("reason", "")
        if not reason.strip():
            problems.append(f"{rel} 的挂起**没有理由**——基线里补 `reason`（为什么还没实现）")
    for key in sorted(now_scn):
        reason = (base_scn.get(key, {}) or {}).get("reason", "")
        if not reason.strip():
            problems.append(f"{key[0]} 场景「{key[1]}」的挂起**没有理由**——基线里补 `reason`")
    return problems


def load_baseline(root: pathlib.Path) -> dict:
    path = root / FEATURES / BASELINE_NAME
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(root: pathlib.Path, baseline: dict | None = None) -> pathlib.Path:
    """刷新基线：**保留已有 reason**（避免刷新一次把理由全抹掉）。"""
    old = baseline if baseline is not None else load_baseline(root)
    counts, draft_files, draft_scenarios = _parse_features(root / FEATURES)
    old_files = old.get("draft_files", {})
    old_scn = {(e["file"], e["name"]): e for e in old.get("draft_scenarios", [])}
    data = {
        "note": (
            "BDD 挂起基线（@draft/@wip/@future 的集合 + 理由）。改动必须走 "
            "`python -m scripts.check_bdd_baseline --update` 并在 LEDGER 留痕——"
            "本文件是「哪些场景没进门禁」的唯一事实源。"
        ),
        "active": counts,
        "draft_files": {
            rel: {
                "tags": entry["tags"],
                "reason": (old_files.get(rel, {}) or {}).get("reason", entry["reason"]),
            }
            for rel, entry in sorted(draft_files.items())
        },
        "draft_scenarios": [
            {
                "file": entry["file"],
                "name": entry["name"],
                "tags": entry["tags"],
                "reason": (old_scn.get((entry["file"], entry["name"]), {}) or {}).get(
                    "reason", entry["reason"]
                ),
            }
            for entry in sorted(draft_scenarios, key=lambda e: (e["file"], e["name"]))
        ],
    }
    path = root / FEATURES / BASELINE_NAME
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def self_test() -> int:
    """S1 注入自检：四类违例必须各命中一条，合规样本不得误报。"""
    bad = 0
    with tempfile.TemporaryDirectory(prefix="bdd-baseline-selftest-") as d:
        root = pathlib.Path(d)
        (root / FEATURES).mkdir(parents=True)
        (root / BEHAVE_INI).write_text(
            "[behave]\ntags = ~@wip\n    ~@future\n    ~@draft\n", encoding="utf-8"
        )
        good = root / FEATURES / "good.feature"
        good.write_text(
            "# language: zh-CN\n功能: 正常集\n"
            "  场景: 会跑的场景\n"
            "    假如 一步\n"
            "  场景大纲: 大纲\n"
            "    假如 参数 <x>\n"
            "  例子:\n"
            "    | x |\n"
            "    | 1 |\n"
            "    | 2 |\n",
            encoding="utf-8",
        )
        draft = root / FEATURES / "drafty.feature"
        draft.write_text(
            "# language: zh-CN\n@draft\n功能: 挂起集\n  场景: 挂起的场景\n    假如 一步\n",
            encoding="utf-8",
        )
        # 合规基线：活动面 3 场景（1 + 2 大纲行）/ 4 步（1 + 1×2 + 背景 0）
        write_baseline(root)
        baseline = load_baseline(root)
        baseline["draft_files"]["features/drafty.feature"]["reason"] = "后端未实现（自检样本）"
        (root / FEATURES / BASELINE_NAME).write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        got = find_problems(root, load_baseline(root))
        if got:
            print(f"✗ S1 合规样本误报：{got}")
            bad += 1

        # ① 静默挂起：给会跑的场景加 @draft
        good.write_text(
            "# language: zh-CN\n功能: 正常集\n  场景: 会跑的场景\n    假如 一步\n  @draft\n  场景: 偷偷挂起\n    假如 一步\n",
            encoding="utf-8",
        )
        got = find_problems(root, load_baseline(root))
        if not any("新增挂起" in p for p in got):
            print(f"✗ S1 「静默新增挂起」未命中：{got}")
            bad += 1

        # ② 理由缺失：挂起文件在基线里没有 reason
        good.write_text(
            "# language: zh-CN\n功能: 正常集\n  场景: 会跑的场景\n    假如 一步\n",
            encoding="utf-8",
        )
        base2 = load_baseline(root)
        base2["draft_files"]["features/drafty.feature"]["reason"] = ""
        (root / FEATURES / BASELINE_NAME).write_text(
            json.dumps(base2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        got = find_problems(root, load_baseline(root))
        if not any("没有理由" in p for p in got):
            print(f"✗ S1 「挂起无理由」未命中：{got}")
            bad += 1
        base2["draft_files"]["features/drafty.feature"]["reason"] = "后端未实现（自检样本）"
        (root / FEATURES / BASELINE_NAME).write_text(
            json.dumps(base2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        # ③ 删场景让门禁变绿（活动面下降）
        good.write_text("# language: zh-CN\n功能: 正常集\n", encoding="utf-8")
        got = find_problems(root, load_baseline(root))
        if not any("活动面" in p and "scenarios" in p for p in got):
            print(f"✗ S1 「活动面下降」未命中：{got}")
            bad += 1

        # ④ behave.ini 不再排除 @draft（盲区形状）
        (root / BEHAVE_INI).write_text("[behave]\ntags = ~@wip\n    ~@future\n", encoding="utf-8")
        got = find_problems(root, load_baseline(root))
        if not any("不再排除" in p for p in got):
            print(f"✗ S1 「behave.ini 排除行被删」未命中：{got}")
            bad += 1

        # S2 空扫自检：features 目录塌缩
        (root / BEHAVE_INI).write_text(
            "[behave]\ntags = ~@wip\n    ~@future\n    ~@draft\n", encoding="utf-8"
        )
        for f in (root / FEATURES).glob("*.feature"):
            f.unlink()
        got = find_problems(root, load_baseline(root))
        if not any("扫描面塌缩" in p for p in got):
            print(f"✗ S2 空扫自检未命中：{got}")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（静默挂起/无理由/活动面下降/排除行被删 四类全命中 + 合规零误报）")
    return 0


def main(argv: list[str]) -> int:
    if "--update" in argv:
        path = write_baseline(ROOT)
        print(f"✓ 基线已刷新：{path.relative_to(ROOT)}（请补 reason 并在 LEDGER 留痕）")
        return 0
    baseline = load_baseline(ROOT)
    if not baseline:
        print(f"✗ 基线文件缺失：{FEATURES}/{BASELINE_NAME}——先跑 `--update` 生成")
        return 1
    problems = find_problems(ROOT, baseline)
    if problems:
        print(f"[bdd-baseline] ✗ 挂起基线不一致（{len(problems)} 处）：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    active = baseline["active"]
    print(
        f"[bdd-baseline] ✓ 挂起基线与实现一致：活动 {active['features']} features / "
        f"{active['scenarios']} scenarios / {active['steps']} steps；"
        f"挂起 {len(baseline['draft_files'])} 个文件 + "
        f"{len(baseline['draft_scenarios'])} 个场景（均写明理由）；behave.ini 仍排除三类标签"
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main(sys.argv[1:]))
