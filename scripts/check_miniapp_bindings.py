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


def main() -> int:
    problems: list[str] = []
    for base in (PAGES, COMPONENTS):
        if not base.is_dir():
            continue
        for wxml in base.rglob("*.wxml"):
            js = wxml.with_suffix(".js")
            if not js.is_file():
                continue
            used = consumed_names(wxml.read_text(encoding="utf-8"))
            if not used:
                continue
            have = written_names(js.read_text(encoding="utf-8"))
            missing = sorted(used - have)
            # 组件/页面通用属性与框架注入名放行
            missing = [m for m in missing if m not in {"item", "index", "true", "false"}]
            if missing:
                problems.append(f"{wxml.relative_to(ROOT)} 读了但 js 从未写入 data: {missing}")
    if problems:
        print("✗ 小程序数据面断链：")
        for p in problems:
            print("  " + p)
        return 1
    print("✓ 小程序数据面一致（wxml 读到的顶层变量都有 data 来源）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
