#!/usr/bin/env python
# scripts/check_theme_tokens.py — 主题令牌对账（第一百六十七批，P2-1/P2-6/P2-7）
"""**主题令牌双向对账**：`cssVars()` 定义集 vs CSS/TSX 的 `var(--…)` 使用集。

背景（164 批专家复核 P2-1）：`cssVars()` 注入了 72 个 CSS 变量，实际只有 71 个被消费，
其中 20 个是"注入了但零消费"（`--sider-width` / `--content-padding` / `--table-row-height` /
`--color-mask` / `--font-mono` …）。结果是"换主题只改 preset"的承诺打折：改这些值不会有反应。

167 批的处置口径（写进 `docs/32` §一 / §五）：
  · **CSS 变量只注入 CSS 实际消费的变量**；antd / JS 直接消费的令牌
    （`colorLink` / `controlHeightSM` / `siderWidth` / `fontFamilyCode` …）不进 CSS 变量；
  · 图表色板 `--chart-1..6` 是**契约**：页面当前只取其中几色，6 色全部放行；
  · 其余零消费即判红——新增令牌必须同时接消费点（CSS `var()` 或 buildAntdTheme/getTheme）。

两条规则：
  **T1 零消费**：`cssVars()` 定义、但全仓没有 `var(--x)` 使用，且不在放行名单 → 红。
  **T2 未定义**：全仓 `var(--x)` 使用、但既不在 `cssVars()` 也不在 CSS 里定义
      （`skins/*.css` 的别名、`PreviewImage` 的 `setProperty` 动态变量）→ 红。

自证：
  · S1 注入自检：T1/T2 必须命中；图表色板/皮肤别名/动态变量不得误报；
  · S2 空扫描自检：扫到 0 个源码文件 = FAIL（防路径写错 → 全绿）。

用法：`python scripts/check_theme_tokens.py [--self-test]`
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "admin-web" / "src"
THEME_INDEX = SRC / "theme" / "index.ts"

#: `cssVars()` 里的 `"--name":` 定义
DEFINED_RE = re.compile(r'^\s*"(--[a-z0-9-]+)"\s*:', re.MULTILINE)
#: CSS / TSX 里的 var() 使用
USED_RE = re.compile(r"var\(\s*(--[a-zA-Z0-9-]+)")
#: CSS 里的变量定义（皮肤别名），以及 setProperty 动态定义
CSS_DEF_RE = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:", re.MULTILINE)
SETPROP_RE = re.compile(r'setProperty\(\s*["\'](--[a-zA-Z0-9-]+)')
#: 图表色板契约：6 色是主题契约的一部分，允许页面只取其中几色
PALETTE_PREFIX = "--chart-"

SOURCE_GLOBS = ("*.css", "*.tsx", "*.ts")


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def defined_vars(index_text: str) -> set[str]:
    """`cssVars()` 显式定义 + 循环注入的 `--chart-1..6`。"""
    defined = set(DEFINED_RE.findall(index_text))
    defined |= {f"{PALETTE_PREFIX}{i}" for i in range(1, 7)}
    return defined


def collect(root: pathlib.Path) -> tuple[set[str], set[str], set[str], int]:
    """返回 (cssVars 定义, var() 使用, CSS/动态定义, 扫描文件数)。"""
    index_text = _read(root / "theme" / "index.ts")
    defined = defined_vars(index_text)
    used: set[str] = set()
    css_defined: set[str] = set()
    count = 0
    for pattern in SOURCE_GLOBS:
        for path in root.rglob(pattern):
            count += 1
            text = _read(path)
            if path.suffix == ".css":
                css_defined |= set(CSS_DEF_RE.findall(text))
            css_defined |= set(SETPROP_RE.findall(text))
            used |= set(USED_RE.findall(text))
    return defined, used, css_defined, count


def audit(
    defined: set[str],
    used: set[str],
    css_defined: set[str],
    allow_prefix: str = PALETTE_PREFIX,
) -> tuple[list[str], list[str]]:
    """纯函数：返回 (零消费, 未定义)。自证与主流程共用。"""
    zero = sorted(name for name in defined - used if not name.startswith(allow_prefix))
    undefined = sorted(used - defined - css_defined)
    return zero, undefined


def _scan(root: pathlib.Path) -> tuple[list[str], int]:
    if not (root / "theme" / "index.ts").exists():
        return ["找不到 theme/index.ts（路径写错会导致假绿）"], 0
    defined, used, css_defined, count = collect(root)
    if count == 0:
        return ["扫描面为空（没有 css/tsx/ts）——路径写错会导致假绿"], 0
    zero, undefined = audit(defined, used, css_defined)
    problems = [f"T1 零消费令牌：{name}" for name in zero]
    problems += [f"T2 未定义令牌：{name}" for name in undefined]
    return problems, count


def self_test() -> int:
    bad = 0
    if audit({"--a", "--unused"}, {"--a"}, set())[0] != ["--unused"]:
        print("✗ S1 T1 未命中：零消费令牌")
        bad += 1
    if audit({"--a"}, {"--a", "--ghost"}, set())[1] != ["--ghost"]:
        print("✗ S1 T2 未命中：未定义令牌")
        bad += 1
    if audit({"--chart-2"}, set(), set())[0]:
        print("✗ S1 误报：图表色板契约被当成零消费")
        bad += 1
    if audit({"--a"}, {"--a", "--paint-ink"}, {"--paint-ink"})[1]:
        print("✗ S1 误报：皮肤 CSS 里定义的别名被当成未定义")
        bad += 1
    if audit({"--a"}, {"--a", "--pv-dx"}, {"--pv-dx"})[1]:
        print("✗ S1 误报：setProperty 动态变量被当成未定义")
        bad += 1
    if audit({"--a"}, {"--a"}, set())[0] or audit({"--a"}, {"--a"}, set())[1]:
        print("✗ S1 误报：合法一对一映射被拦")
        bad += 1
    with tempfile.TemporaryDirectory(prefix="theme-tokens-selftest-") as d:
        problems, _ = _scan(pathlib.Path(d))
        if not problems:
            print("✗ S2 空扫描未判红")
            bad += 1
    if bad:
        print(f"✗ S1/S2 注入自检失败 {bad} 项")
        return 1
    print(
        "✓ S1/S2 自检通过（T1 零消费 / T2 未定义均命中；图表色板、皮肤别名、"
        "setProperty 动态变量、合法映射零误报；空扫描判红）"
    )
    return 0


def main() -> int:
    problems, count = _scan(SRC)
    if problems:
        print(f"FOUND {len(problems)} 处主题令牌问题（扫描 {count} 个文件）：")
        for p in problems:
            print("  ✗", p)
        print(
            "\nFAIL: cssVars() 只注入 CSS 实际消费的变量；antd/JS 消费的令牌不要重复注入；"
            "新增令牌必须同时接消费点（见 docs/32 §一 / §五）"
        )
        return 1
    defined, used, css_defined, _ = collect(SRC)
    zero, undefined = audit(defined, used, css_defined)
    print(
        f"PASS: 主题令牌对账通过（定义 {len(defined)} / 使用 {len(used)} / "
        f"零消费 {len(zero)}（仅图表色板契约）/ 未定义 {len(undefined)}）✓"
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
