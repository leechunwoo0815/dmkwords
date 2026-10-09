#!/usr/bin/env python
"""小程序接线检查（2026-10-09，A1-11）——两条"现有七查读不到"的类。

为什么新增（上线前审查 P0-2 / P2-14；错误记忆库 §一百零七 同族）：

  **W1 `bind*` handler 必须存在**：`refund-apply.wxml` 写 `bindtap="onSubmit"`，而
  `refund-apply.js` 里**从未定义** `onSubmit` → 家长选好订单、填完原因、点「提交退款申请」
  **毫无反应**（微信对缺失 handler 静默忽略），资金类入口形同虚设。现有
  `check_miniapp_bindings` 只查**数据面**（wxml 读的变量是否进过 data），不看 handler 是否存在。

  **W2 插值类名必须能解析到样式**：`class="cat-{{item.cat}}"` 这类写法里，前缀片段（`cat-`）
  必须在**同页作用域** wxss 里有对应类定义（如 `.cat-english`），否则状态配色**静默失效**；
  R12（悬空类名）/R16（两端对账）只看静态类名，未加引号的插值片段两处都测不到。

判据与自证：
  · 两类违例都必须为 0（新规则不设基线）；
  · S1 注入自检：内置违例样本必须被检出（防检查器自身假绿）；
  · S2 空扫描自检：扫到 0 个 wxml = FAIL（防"路径写错→全绿"的盲区形状）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINIAPP = ROOT / "miniapp"
SCAN_DIRS = (MINIAPP / "pages", MINIAPP / "components")

BIND_RE = re.compile(r"\b(?:bind|catch)([a-z]+)\s*=\s*\"([^\"]*)\"")
CLASS_ATTR_RE = re.compile(r"class\s*=\s*\"([^\"]*)\"")
INTERP_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
CLASS_DEF_RE = re.compile(r"\.([A-Za-z][\w-]*)")
IMPORT_RE = re.compile(r"@import\s+\"([^\"]+)\";")


def js_defines(js_text: str, name: str) -> bool:
    """js 里是否存在该方法（三种写法：`name(` / `name: (` / `name: function (`，均可带 async）。"""
    patterns = (
        rf"^\s*(?:async\s+)?{re.escape(name)}\s*\(",
        rf"^\s*{re.escape(name)}\s*:\s*(?:async\s*)?(?:function\s*)?\(",
    )
    return any(re.search(p, js_text, re.M) for p in patterns)


def scope_classes(wxml: Path, app_wxss: Path) -> set[str]:
    """同页作用域的类名集合：同目录 wxss（含 @import 链）+ app.wxss。"""
    out: set[str] = set()
    seen: set[Path] = set()

    def absorb(path: Path) -> None:
        if path in seen or not path.is_file():
            return
        seen.add(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for rel in IMPORT_RE.findall(text):
            absorb((path.parent / rel).resolve())
        for m in CLASS_DEF_RE.finditer(text):
            out.add(m.group(1))

    absorb(wxml.with_suffix(".wxss"))
    absorb(app_wxss)
    return out


def static_values(expr: str) -> list[str]:
    """能静态求值的插值 → 具体类名后缀（只认字面量与 `X % N`，N ≤ 12；其余返回空）。"""
    e = (expr or "").strip()
    if e.isdigit():
        return [e]
    m = re.fullmatch(r"[\w\s.]+%\s*(\d+)", e)
    if m and int(m.group(1)) <= 12:
        return [str(i) for i in range(int(m.group(1)))]
    return []


def scan(wxml_files: list[Path], app_wxss: Path | None = None) -> list[str]:
    app_wxss = app_wxss or (MINIAPP / "app.wxss")
    problems: list[str] = []
    for wxml in wxml_files:
        text = wxml.read_text(encoding="utf-8", errors="ignore")
        try:
            rel = wxml.relative_to(ROOT)
        except ValueError:  # 自检样本在临时目录
            rel = wxml
        js_path = wxml.with_suffix(".js")
        js_text = js_path.read_text(encoding="utf-8", errors="ignore") if js_path.is_file() else ""
        # W1：bind*/catch* 指向的方法必须存在（插值形式无法静态判定，跳过）
        for _event, raw in BIND_RE.findall(text):
            name = raw.strip()
            if not name or "{{" in name:
                continue
            if not js_text:
                problems.append(f"W1 {rel}: 有 {name} 绑定但同名 js 不存在")
            elif not js_defines(js_text, name):
                problems.append(
                    f"W1 {rel}: bind/catch 指向的 {name}() 在 {js_path.name} 里没有定义"
                )
        # W2：class 属性里的插值片段必须能解析到样式
        #   ① 前缀级：`cat-{{x}}` 至少要存在一个 `.cat-*` 定义；
        #   ② 可静态求值：`banner-{{index % 3}}` → 逐值要求 `.banner-0/1/2` 存在
        #      （数据驱动的插值枚举不出来，那类由 R16b 的"JS 提供的类名必须存在"兜）。
        classes = scope_classes(wxml, app_wxss)
        for value in CLASS_ATTR_RE.findall(text):
            if "{{" not in value:
                continue
            parts = INTERP_RE.split(value)  # [字面量, 表达式, 字面量, 表达式, ..., 字面量]
            for i in range(0, len(parts) - 1, 2):
                frag = (parts[i] or "").strip().split(" ")[-1] if (parts[i] or "").strip() else ""
                if not re.fullmatch(r"[A-Za-z][\w-]*-", frag):
                    continue
                values = static_values(parts[i + 1])
                if values:
                    for suffix in values:
                        if f"{frag}{suffix}" not in classes:
                            problems.append(
                                f"W2 {rel}: 插值类名 `{frag}{suffix}` 在本页作用域 wxss 里没有"
                                f"定义（状态样式会静默失效）"
                            )
                elif not any(c.startswith(frag) for c in classes):
                    problems.append(
                        f"W2 {rel}: 插值类名前缀 `{frag}` 在本页作用域 wxss 里没有对应类定义"
                        f"（状态样式会静默失效）"
                    )
    return problems


def selftest() -> list[str]:
    """S1 注入自检：违例样本必须命中（两条规则各一）。"""
    import tempfile

    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "app.wxss").write_text(".only-app {}\n", encoding="utf-8")
        bad = base / "demo.wxml"
        bad.write_text(
            '<view bindtap="onMissing" class="cat-{{item.cat}}">x</view>\n', encoding="utf-8"
        )
        (base / "demo.wxss").write_text(".other {}\n", encoding="utf-8")
        (base / "demo.js").write_text("Page({\n  onLoad() {},\n})\n", encoding="utf-8")
        hits = scan([bad], app_wxss=base / "app.wxss")
        if not any(p.startswith("W1") for p in hits):
            fails.append("S1: W1 未命中注入样本")
        if not any(p.startswith("W2") for p in hits):
            fails.append("S1: W2 未命中注入样本")
        # 正例：handler 存在 + 前缀有定义 → 必须零命中
        good = base / "ok.wxml"
        good.write_text('<view bindtap="onTap" class="cat-{{c}}">x</view>\n', encoding="utf-8")
        (base / "ok.js").write_text("Page({\n  onTap() {},\n})\n", encoding="utf-8")
        (base / "ok.wxss").write_text(".cat-english {}\n", encoding="utf-8")
        good_hits = scan([good], app_wxss=base / "app.wxss")
        if good_hits:
            fails.append(f"S1: 正例被误报：{good_hits}")
    return fails


def main() -> int:
    wxml_files = sorted(p for d in SCAN_DIRS if d.is_dir() for p in d.rglob("*.wxml"))
    if len(wxml_files) < 10:  # S2：盲区形状（路径写错 / 扫描面塌缩）
        print(f"FAIL(S2): 只扫到 {len(wxml_files)} 个 wxml——扫描面异常，检查 SCAN_DIRS")
        return 1
    self_fails = selftest()
    if self_fails:
        print("FAIL(S1): " + "；".join(self_fails))
        return 1
    problems = scan(wxml_files)
    print(f"扫描面: {len(wxml_files)} 个 wxml（pages + components）")
    print(f"W1 bind*/catch* handler 缺失: {sum(1 for p in problems if p.startswith('W1'))}")
    print(f"W2 插值类名无样式定义: {sum(1 for p in problems if p.startswith('W2'))}")
    if problems:
        print("\n违例明细：")
        for p in problems:
            print("  ✗", p)
        print("\nFAIL: 接线检查不通过（W1/W2 必须为 0）")
        return 1
    print("PASS: 小程序接线两条规则全零（handler 存在 / 插值类名可解析）✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
