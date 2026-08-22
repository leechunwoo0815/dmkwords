#!/usr/bin/env python3
"""验证管理端 data-action 事件绑定完整性 — CI Gate 10

按页核查拓扑：
  1. page.html (所有 data-action) + page.js (动态渲染的 data-action)
     → 必须能在 page.js handler 分支或全局池中找到
  2. base.html (所有 data-action)
     → 必须能在全局池中找到（base.html 不绑定特定页面 handler）

handler 模式覆盖（共 7 种）：
  a. action === 'x' / action == 'x'
  b. 'x' === action / 'x' == action
  c. closest('[data-action="x"]')
  d. querySelector('[data-action="x"]')
  e. getAttribute('data-action') === 'x'
  f. dataset.action === 'x' / dataset['action'] === 'x'
  g. case 'x':

豁免机制：
  - HTML 注释: <!-- wiring-exempt: action-name -->
  - JS 注释:   // wiring-exempt: action-name
  - 已知豁免: logout (admin.js .logout-btn), toggle-target-user (message_manage.js #msgTarget),
              load-data (reading_data.js .period-tabs), override (damage_reports select option)

退出码：0=通过 1=有死动作（--strict）或始终 0（默认报告模式）
"""

import argparse
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TEMPLATES = BASE / "backend" / "templates" / "admin"
JS_PAGES = BASE / "backend" / "static" / "admin" / "js" / "pages"
JS_GLOBAL_DIR = BASE / "backend" / "static" / "admin" / "js"
JS_GLOBAL_FILES = [
    JS_GLOBAL_DIR / "admin.js",
    JS_GLOBAL_DIR / "base-init.js",
    JS_GLOBAL_DIR / "admin-pages.js",
]

# ── 已知豁免：通过 id/class 绑定（非 data-action 模式）──
KNOWN_EXEMPTIONS = {
    "logout",  # admin.js:277 .logout-btn click → auth.logout()
    "toggle-target-user",  # message_manage.js:151 #msgTarget change
    "load-data",  # reading_data.js .period-tabs container delegation
    "override",  # damage_reports.js select option value (not data-action)
}

# ── handler 正则模式（7 种）──
_HANDLER_PATTERNS = [
    # a: action === 'x' / action == 'x'
    re.compile(r"""action\s*=={1,2}\s*["']([^"']+)["']"""),
    # b: 'x' === action / 'x' == action
    re.compile(r"""["']([^"']+)["']\s*=={1,2}\s*action"""),
    # c: closest('[data-action="x"]') / closest("[data-action='x']")
    re.compile(r"""closest\(\s*['"][^""]*\[data-action\s*=\s*(['"])([^\1]+?)\1[^""]*\]"""),
    # d: querySelector('[data-action="x"]')
    re.compile(r"""querySelector\(\s*['"][^""]*\[data-action\s*=\s*(['"])([^\1]+?)\1[^""]*\]"""),
    # e: getAttribute('data-action') === 'x'
    re.compile(r"""getAttribute\(\s*['"]data-action['"]\s*\)\s*=={1,2}\s*["']([^"']+)["']"""),
    # f: dataset.action === 'x' / dataset['action'] === 'x'
    re.compile(r"""dataset\.action\s*=={1,2}\s*["']([^"']+)["']"""),
    re.compile(r"""dataset\[['"]action['"]\]\s*=={1,2}\s*["']([^"']+)["']"""),
    # g: case 'x':
    re.compile(r"""case\s+["']([^"']+)["']\s*:"""),
]


def extract_html_actions(content: str) -> set[str]:
    """从 HTML 内容提取所有 data-action="xxx"（属性级精确匹配）"""
    return {m.group(1) for m in re.finditer(r'data-action\s*=\s*["\']([^"\']+)["\']', content)}


def extract_js_render_actions(content: str) -> set[str]:
    """从 JS 内容提取 data-action="xxx" 字面量（用于检测动态渲染的 action）"""
    return {m.group(1) for m in re.finditer(r'data-action\s*=\s*["\']([^"\']+)["\']', content)}


def extract_js_handlers(js_path: Path) -> set[str]:
    """从 JS 文件提取所有匹配的 data-action handler（7 种模式）

    模式 a/b (action === 'x') 仅在文件没有用 .value 赋给 action 变量时匹配
    （防止混淆 select.value 与 getAttribute('data-action')）。
    模式 c-g 直接匹配（它们明确引用 data-action 属性）。
    """
    content = js_path.read_text(encoding="utf-8")
    handlers: set[str] = set()
    lines = content.split("\n")

    # 判断 action 变量来源：如果文件有 .value 赋给 action，禁用模式 a/b
    has_action_from_value = bool(re.search(r"""action\s*=\s*(.*?)\.value""", content))

    for m in _HANDLER_PATTERNS:
        # 跳过模式 a/b 当 action 来自 .value
        if has_action_from_value and m in (_HANDLER_PATTERNS[0], _HANDLER_PATTERNS[1]):
            # 仅当 ±10 行内有 getAttribute('data-action') 时才匹配
            for i, line in enumerate(lines):
                lm = m.search(line)
                if lm:
                    ctx = "\n".join(lines[max(0, i - 10) : min(len(lines), i + 11)])
                    if re.search(r"""getAttribute\s*\(\s*['"]data-action['"]\s*\)""", ctx):
                        handlers.add(lm.group(1))
            continue
        for match in m.finditer(content):
            # 模式 c/d 有两个捕获组，取最后一个
            groups = match.groups()
            handlers.add(groups[-1] if len(groups) > 1 else groups[0])

    return handlers


def extract_wiring_exemptions(content: str) -> set[str]:
    """从内容提取 wiring-exempt 注释标记的豁免动作（HTML + JS）"""
    exemptions: set[str] = set()
    for m in re.finditer(r"(?:<!--|#|//)\s*wiring-exempt:\s*([^\n\->]+)", content):
        for name in re.split(r"[,，\s]+", m.group(1).strip()):
            name = name.strip()
            if name:
                exemptions.add(name)
    return exemptions


def filename_to_page_basename(html_name: str) -> str:
    """将 'teachers.html' 转为 'teachers.js'"""
    return html_name.replace(".html", ".js")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 data-action 绑定完整性")
    parser.add_argument("--strict", action="store_true", help="严格模式：发现任何问题即返回 1")
    args = parser.parse_args()

    has_error = False

    # ── 加载全局池 handler ──
    global_handlers: set[str] = set()
    global_render_actions: set[str] = set()
    global_exemptions: set[str] = set()
    for gf in JS_GLOBAL_FILES:
        if gf.exists():
            content = gf.read_text(encoding="utf-8")
            global_handlers.update(extract_js_handlers(gf))
            global_render_actions.update(extract_js_render_actions(content))
            global_exemptions.update(extract_wiring_exemptions(content))

    print(f"[INFO] 全局池 handler: {len(global_handlers)} 个")
    if global_handlers:
        for h in sorted(global_handlers):
            print(f"  · {h} (全局池)")

    # ── 加载所有 pages/*.js 的 handler、render、和豁免 ──
    page_js_handlers: dict[str, set[str]] = {}
    page_js_renders: dict[str, set[str]] = {}
    page_js_exemptions: dict[str, set[str]] = {}

    for js_file in sorted(JS_PAGES.glob("*.js")):
        fname = js_file.name
        content = js_file.read_text(encoding="utf-8")
        handlers = extract_js_handlers(js_file)
        if handlers:
            page_js_handlers[fname] = handlers
        renders = extract_js_render_actions(content)
        if renders:
            page_js_renders[fname] = renders
        exemptions = extract_wiring_exemptions(content)
        if exemptions:
            page_js_exemptions[fname] = exemptions

    print(f"\n[INFO] pages/*.js handler 文件: {len(page_js_handlers)} 个")
    print(f"[INFO] pages/*.js render 文件: {len(page_js_renders)} 个")

    # ── 按页核查 ──
    print("\n" + "=" * 60)
    print("按页核查（page.html + page.js render ↔ page.js handler + 全局池）")
    print("=" * 60)

    all_html_actions_per_page: dict[str, set[str]] = {}
    all_render_actions_per_page: dict[str, set[str]] = {}
    dead_actions_detail: dict[str, dict] = {}  # page → {action: [sources]}

    for html_file in sorted(TEMPLATES.glob("*.html")):
        page = html_file.name
        js_name = filename_to_page_basename(page)

        # 提取本页 HTML data-action
        html_content = html_file.read_text(encoding="utf-8")
        html_actions = extract_html_actions(html_content)
        all_html_actions_per_page[page] = html_actions

        # 提取本页 HTML wiring-exempt
        html_exemptions = extract_wiring_exemptions(html_content)

        # 提取本页 JS render
        js_render_actions = page_js_renders.get(js_name, set())
        all_render_actions_per_page[page] = js_render_actions

        # 本页需要处理的 action = HTML + JS render
        page_actions = html_actions | js_render_actions
        if not page_actions:
            continue

        # 本页可用 handler = 对应 page.js + 全局池
        page_handlers = set(global_handlers)
        if js_name in page_js_handlers:
            page_handlers.update(page_js_handlers[js_name])

        # 本页豁免 = 全局豁免 + 已知豁免 + 本页 HTML/JS 注释豁免
        page_exemptions = page_js_exemptions.get(js_name, set())
        all_exemptions = KNOWN_EXEMPTIONS | global_exemptions | page_exemptions | html_exemptions

        # 检测死动作
        dead = page_actions - page_handlers - all_exemptions

        if dead:
            has_error = True
            dead_detail = {}
            for action in sorted(dead):
                sources = []
                if action in html_actions:
                    sources.append("html")
                if action in js_render_actions:
                    sources.append("js:render")
                dead_detail[action] = sources
            dead_actions_detail[page] = dead_detail
            print(f"\n❌ {page}: {len(dead)} 个死动作")
            for action, sources in dead_detail.items():
                print(f"  · {action} (来源: {', '.join(sources)})")
        else:
            if page_actions:
                print(f"  ✅ {page}: {len(page_actions)} 个 action, 全部匹配")

    # ── base.html 特殊处理（仅用全局池 handler）──
    base_file = TEMPLATES / "base.html"
    if base_file.exists():
        base_content = base_file.read_text(encoding="utf-8")
        base_actions = extract_html_actions(base_content)
        base_exemptions = extract_wiring_exemptions(base_content)
        all_exemptions_base = KNOWN_EXEMPTIONS | global_exemptions | base_exemptions
        base_dead = base_actions - global_handlers - all_exemptions_base
        if base_dead:
            has_error = True
            print(f"\n❌ base.html: {len(base_dead)} 个死动作")
            for a in sorted(base_dead):
                print(f"  · {a}")
        elif base_actions:
            print(f"\n  ✅ base.html: {len(base_actions)} 个 action, 全部匹配")

    # ── 孤儿处理检测（按页）──
    print("\n" + "=" * 60)
    print("孤儿处理检测（JS 有 handler 但本页 HTML/render 无引用）")
    print("=" * 60)

    orphan_found = False
    for js_file in sorted(JS_PAGES.glob("*.js")):
        fname = js_file.name
        handlers = page_js_handlers.get(fname, set())
        if not handlers:
            continue

        html_name = fname.replace(".js", ".html")
        html_acts = all_html_actions_per_page.get(html_name, set())
        render_acts = all_render_actions_per_page.get(html_name, set())
        page_actions = html_acts | render_acts

        orphans = handlers - page_actions
        if orphans:
            # 只在孤儿不在任何 HTML 或全局 render 中时才报告
            orphans_in_any_html: set[str] = set()
            for acts in all_html_actions_per_page.values():
                orphans_in_any_html.update(orphans & acts)
            orphans_in_global_render = orphans & global_render_actions
            real_orphans = orphans - orphans_in_any_html - orphans_in_global_render

            if real_orphans:
                orphan_found = True
                print(f"\n  ⚠ {fname}: {len(real_orphans)} 个孤儿处理")
                for a in sorted(real_orphans):
                    print(f"    · {a}")

    if not orphan_found:
        print("  ✅ 无孤儿处理 — 所有 handler 均有对应 data-action 引用")

    # ── data-pg 模式检查 ──
    print("\n" + "=" * 60)
    print("data-pg 模式检查")
    print("=" * 60)
    for js_file in sorted(JS_PAGES.glob("*.js")):
        content = js_file.read_text(encoding="utf-8")
        pg_actions = {
            m.group(1) for m in re.finditer(r'data-pg\s*=\s*["\']([^"\']+)["\']', content)
        }
        if pg_actions:
            pg_handlers = set()
            for pg in pg_actions:
                if re.search(
                    r"window\.\w+Page\s*=\s*\{[^}]*\b" + re.escape(pg) + r"\b",
                    content,
                ) or re.search(
                    r"(?:function\s+)?\b" + re.escape(pg) + r"\s*(?:=|:?\s*function\s*\()",
                    content,
                ):
                    pg_handlers.add(pg)
            missing_pg = pg_actions - pg_handlers
            if missing_pg:
                has_error = True
                print(f"  ❌ {js_file.name}: {len(missing_pg)} 个 data-pg 可能缺失函数")
                for a in sorted(missing_pg):
                    print(f"    · {a}")
            else:
                print(f"  ✅ {js_file.name}: {len(pg_actions)} 个 data-pg, 全部匹配")

    # ── 汇总 ──
    print()
    if has_error:
        total_dead = sum(len(v) for v in dead_actions_detail.values())
        msg = f"❌ verify_action_wiring: 不通过 — {total_dead} 个死动作"
        print(msg)
        if args.strict:
            return 1
        else:
            print("⚠ （传递 --strict 则返回 1）")
            return 0
    else:
        print("✅ verify_action_wiring: 通过 — 所有 data-action 绑定完整")
        return 0


if __name__ == "__main__":
    sys.exit(main())
