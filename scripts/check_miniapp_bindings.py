# scripts/check_miniapp_bindings.py — 小程序数据面断链检查（错误记忆库 §六十三 防复发）
"""查什么：**wxml 里读的每个顶层变量，是否真的在 js 里进过 data**。

为什么要它（E-20260912-01）：金光播报连续两轮"算式算对了却不显示"——第一次是
`setData` 漏字段，第二次是**装饰层漏字段**（`adminDetail.content` 取到 undefined）。
这两类都是"值没到 data"，靠人眼 grep 变量名会漏，必须机器查。

做法（保守近似，只报"读了但从未写入"）：
1. 从 `pages/**/*.wxml` 收集 `{{ 根变量 }}`（排除 wx:for 别名与字面量）；
2. 从同名 `*.js` 收集"写入过的 data 键"＝ `data: {...}` 字面量键 + 所有 `setData({...})`
   参数里的 `key:`（**过近似**：嵌套对象的键也算写入 → 宁可漏报不误报）；
3. 输出差集；有差集 → exit 1。

已知例外用页内注释 `<!-- bindings-ignore: foo,bar -->` 声明。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "miniapp" / "pages"
COMPONENTS = ROOT / "miniapp" / "components"

MUSTACHE = re.compile(r"\{\{(.*?)\}\}", re.S)
IDENT = re.compile(r"[A-Za-z_$][\w$]*")
WXFOR_ITEM = re.compile(r'wx:for-item\s*=\s*"([^"]+)"')
WXFOR_ALIAS = re.compile(r'wx:for\s*=\s*"\{\{\s*([A-Za-z_$][\w$]*)\s*\}\}"')
IGNORE = re.compile(r"bindings-ignore:\s*([^\-\->]+)")
LITERALS = {"true", "false", "null", "undefined"}
# wxml 表达式里出现的"属性名/方法名/运算符"白名单（取点号前的根名不会命中它们）
JS_KEY = re.compile(r"([A-Za-z_$][\w$]*)\s*:")


def _strip_paths(expr: str) -> set[str]:
    """表达式 → 根变量集合（`item.name` 取 `item`；字符串/数字/运算符丢掉）。"""
    out: set[str] = set()
    # 去掉字符串字面量（wxml 表达式里可能有 'x' 或 "x"）
    expr = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
    for m in IDENT.finditer(expr):
        name = m.group(0)
        if name in LITERALS or name.isdigit():
            continue
        # 只取"根"：前一个非空字符是 . 的话说明是属性，跳过
        start = m.start()
        prev = expr[:start].rstrip()
        if prev.endswith("."):
            continue
        out.add(name)
    return out


def consumed_names(wxml: str) -> set[str]:
    aliases: set[str] = set(WXFOR_ITEM.findall(wxml))
    aliases |= set(WXFOR_ALIAS.findall(wxml))
    ignored: set[str] = set()
    for m in IGNORE.finditer(wxml):
        ignored |= {x.strip() for x in m.group(1).split(",") if x.strip()}
    names: set[str] = set()
    for m in MUSTACHE.finditer(wxml):
        names |= _strip_paths(m.group(1))
    return names - aliases - ignored


def written_names(js: str) -> set[str]:
    """写入过 data 的键（**过近似**：宁可漏报不误报）。

    覆盖三种写法：① `key: value`；② ES6 简写属性单占一行（`expireLine,`）。
    （首版只匹配 ①，把 `{ expireLine, }` 误报成断链 —— 检查器自身也要先自证。）
    """
    names: set[str] = set(JS_KEY.findall(js))
    names |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*,?\s*$", js, re.M))
    return names


#: 组件/页面通用属性与框架注入名放行（不参与断链判定）
FRAMEWORK_NAMES = frozenset({"item", "index", "true", "false"})


def missing_for(wxml_text: str, js_text: str) -> list[str]:
    """一份 wxml 读了、但同名 js 从未写入 data 的顶层变量（**判定只在这一处**）。

    抽出来是为了自证（第八轮审查 P1-10）：原判定内联在 `main()` 的 rglob 循环里，
    没有入口喂样本 → 判据被改坏也没人知道。
    """
    used = consumed_names(wxml_text)
    if not used:
        return []
    return sorted(m for m in (used - written_names(js_text)) if m not in FRAMEWORK_NAMES)


def self_test() -> int:
    """S1 注入自检：断链必须命中；别名/忽略标记/简写属性不得误报。"""
    bad = 0
    cases: list[tuple[str, str, str, list[str]]] = [
        (
            "断链",
            "<view>{{ ghostVar }}</view>\n",
            "Page({ data: { real: 1 } })\n",
            ["ghostVar"],
        ),
        (
            "合规",
            '<view wx:for="{{list}}" wx:for-item="row">{{ row.name }}</view>\n',
            "Page({ data: { list: [], row: {} } })\n",
            [],
        ),
        (
            "忽略标记",
            "<!-- bindings-ignore: later --><view>{{ later }}</view>\n",
            "Page({ data: {} })\n",
            [],
        ),
        (
            "ES6 简写属性单占一行（首版误报过）",
            "<view>{{ expireLine }}</view>\n",
            "Page({ data: {\n  expireLine,\n} })\n",
            [],
        ),
        (
            "框架注入名放行",
            '<view wx:for="{{rows}}">{{ index }}{{ item }}</view>\n',
            "Page({ data: { rows: [] } })\n",
            [],
        ),
    ]
    for name, wxml, js, expect in cases:
        got = missing_for(wxml, js)
        if got != expect:
            print(f"✗ S1 [{name}] 期望 {expect}，实得 {got}")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（断链命中 + 别名/忽略/简写/框架名零误报）")
    return 0


def main() -> int:
    problems: list[str] = []
    scanned = 0
    for base in (PAGES, COMPONENTS):
        if not base.is_dir():
            continue
        for wxml in base.rglob("*.wxml"):
            scanned += 1
            js = wxml.with_suffix(".js")
            if not js.is_file():
                continue
            missing = missing_for(wxml.read_text(encoding="utf-8"), js.read_text(encoding="utf-8"))
            if missing:
                problems.append(f"{wxml.relative_to(ROOT)} 读了但 js 从未写入 data: {missing}")
    # S2 空扫自检（2026-10-12 审查 P2）：扫到 0 个 wxml 时"全绿"是盲区形状——
    # 目录改名/移动会让本检查器静默失效（兄弟检查器 check_miniapp_wiring 早有此护栏）。
    if scanned < 10:
        print(f"✗ 扫描面塌缩：只扫到 {scanned} 个 wxml（应 ≥10）——检查器自身失效，判 FAIL")
        return 1
    if problems:
        print("✗ 小程序数据面断链：")
        for p in problems:
            print("  " + p)
        return 1
    print(f"✓ 小程序数据面一致（扫 {scanned} 个 wxml：读到的顶层变量都有 data 来源）")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
