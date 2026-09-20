"""小程序风格基准机械门禁（2026-09-13，任务包-20260913 §五 落地）。

十三条规则（R1–R7 = 最初七条；R8–R11 = 后续插修补入；R12/R13 = 2026-09-16 两批补入）：
  R1 裸色值:    pages/components/custom-tab-bar 的 wxss 禁止色值字面量（app.wxss 定义令牌除外）
  R2 内联样式:  wxml 禁止 style= 写死颜色/尺寸（{{}} 动态绑定除外）；布局原语 WARN
  R3 同义类:    页面 wxss 禁止重复定义公共类（.empty-text/.section-title/...）
  R4 孤儿文件:  pages/ 下未被 app.json 注册且未被 @import 引用的样式/页面文件 = 0
  R5 JSON 值域: app.json 与页面 json 中的 hex 必须精确等于令牌值；页面 json 禁设
                navigationBarBackgroundColor
  R6 规范名:    禁 var(--text-tertiary)（用 --muted）；var(--primary) WARN（用 --accent）
  R7 禁项:      oklch(/backdrop-filter 任意 wxss=0；!important 出 app.wxss=0
  R8 样式语法:   wxss 语法（选择器/声明）解析失败 = 0
  R9 花括号结构: wxss 花括号配对失衡 = 0
  R10 组件变量兜底: 拿不到 page 变量的组件，var(--x) 必须带字面兜底
  R11 非法选择器: 选择器位置出现 var()（会让整个分包 WXSS 编译失败）
  R12 悬空类名:  WXML 用到的类名必须在**本页作用域**（同目录 wxss + app.wxss + @import 链）有定义；
                 别的页面定义了同名类不算（WXSS 按页隔离）。专治「设计做完、模板没接」——
                 WXML 写 `speed-chip-active`、WXSS 定义 `.speed-chip.active` → 选中态永远不亮。
                 已知存量走 R12_BASELINE（只许减不许增），新增直接 FAIL。
  R13 图标槽位:  R13a = 图标槽位（class 含 icon/emoji）里禁止平台 emoji（三端渲染不一致、
                 颜色与令牌无关）；R13b = 引用的图标资产必须真实存在（`/icons/ui/*.png` 与
                 `icon-name`）。排版字形（✓ ✕ ★ ☆ ▶ 等 TYPO_GLYPHS）是设计系统文字符号，
                 在白名单内、不算 emoji（R13a 误报源，已实证）。
  ↑ R1–R7 为最初七条；R8–R11 为后续插修补入；R12/R13 为 2026-09-16 两批补入。
    **以 main() 实际打印的规则清单为准**（历史教训：docstring 只列七条、实现已十一条，
    2026-09-20 又发现漏列 R13——文档与代码不同步是这类检查器的惯犯，改规则必改本清单）。

三重自证（防"检查器自身假绿"）：
  S1 注入自检:  对内置违例样本运行全部检测器，必须全部命中
  S2 空结果自检: 扫描到 0 个 wxss / 0 个注册页 = FAIL（A-1 盲区形状）
  S3 基线数字:  打印 页面/wxss/色值 计数，低于常识下限 = FAIL

豁免白名单：集中在下方 WHITELIST（文件+行内容正则+理由+日期），初始 0 条；
非空时输出中打印全清单。页面 wxss 顶部注释不构成豁免。

⚠ 本检查器不能替代目视：遮挡/覆盖/错位仍需截图证据。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MINIAPP = REPO / "miniapp"

# 豁免白名单: (文件路径后缀, 行内容正则, 理由, 日期)。初始 0 条，遇一例议一例。
WHITELIST: list[tuple[str, str, str, str]] = [
    # 品牌多色渐变/专属 tint：令牌体系无法表达多色渐变与品牌专属色，
    #   按《专家答复书》裁定 5.1「确需裸值（渐变遮罩/品牌金）必须登记」逐条登记。
    (
        "pages/circle/circle.wxss",
        r"#fff2e6|#ffe8f0",
        "赞通知条 桃→粉品牌渐变（HEAD 原值，令牌表达不了）",
        "2026-09-14",
    ),
    (
        "pages/circle/circle.wxss",
        r"#fff8de|#ffe9a8|#f7c552",
        "播报条 三段金渐变（HEAD 原值）",
        "2026-09-14",
    ),
    (
        "pages/circle/profile.wxss",
        r"#fff1c9|#ffe0e8",
        "名片页 奶油→粉渐变（HEAD 原值）",
        "2026-09-14",
    ),
    (
        "pages/reading-pkg/book-detail/book-detail.wxss",
        r"#fdecec|#f5c2c2",
        "逾期警示 淡红实底/描边（HEAD 原值，--error-soft 会冲淡）",
        "2026-09-14",
    ),
    (
        "components/loading-skeleton/loading-skeleton.wxss",
        r"#f4efe2",
        "骨架屏 shimmer 高光带（HEAD 原值）",
        "2026-09-14",
    ),
    (
        "pages/activity-pkg/activity-detail/activity-detail.wxss",
        r"rgba\(38, 36, 25, 0\.04\)",
        "信息卡硬阴影（HEAD 原值，无对应令牌）",
        "2026-09-14",
    ),
    (
        "pages/login/login.wxss",
        r"#07C160",
        "微信品牌绿：微信登录按钮品牌色（页面源码已注释登记 intentional）",
        "2026-09-13",
    ),
]

# 常识下限（S3）：低于即视为扫描范围损坏
MIN_PAGES = 25
MIN_WXSS = 30

COLOR_PROPS = {
    "color",
    "background",
    "background-color",
    "border-color",
    "border-top-color",
    "border-right-color",
    "border-bottom-color",
    "border-left-color",
    "box-shadow",
    "text-shadow",
    "outline-color",
    "fill",
    "stroke",
    "caret-color",
    "text-decoration-color",
}
SIZE_PROPS = {
    "font-size",
    "width",
    "height",
    "min-width",
    "max-width",
    "min-height",
    "max-height",
    "margin",
    "margin-top",
    "margin-right",
    "margin-bottom",
    "margin-left",
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "top",
    "bottom",
    "left",
    "right",
    "border-radius",
    "border-width",
    "line-height",
    "gap",
    "row-gap",
    "column-gap",
    "flex-basis",
    "letter-spacing",
}
LAYOUT_PROPS = {
    "display",
    "flex",
    "flex-direction",
    "justify-content",
    "align-items",
    "align-content",
    "align-self",
    "flex-wrap",
    "position",
    "text-align",
    "overflow",
    "overflow-x",
    "overflow-y",
    "white-space",
    "z-index",
    "transform",
    "vertical-align",
    "object-fit",
    "float",
    "clear",
}
SYNONYM_CLASSES = [
    "empty-text",
    "section-title",
    "list-item",
    "submit-btn",
    "info-row",
    "info-label",
    "info-value",
]
NAMED_COLORS = (
    "white|black|silver|gray|grey|red|maroon|yellow|olive|lime|green|aqua|"
    "cyan|teal|blue|navy|fuchsia|magenta|purple|orange|gold|pink|snow|brown|"
    "ivory|beige|wheat|tan|coral|salmon|khaki|plum|violet|indigo|orchid"
)
RE_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RE_FUNC_COLOR = re.compile(r"\b(rgba?|hsla?|oklch|oklab)\([^)]*\)")
RE_NAMED = re.compile(rf"(?<![\w-])({NAMED_COLORS})(?![\w-])")
RE_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def strip_comments(text: str) -> str:
    """去注释但保留换行数——报告行号与源文件一致。"""
    return RE_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


RE_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*"([^"]*)\"""")
RE_DECL = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
RE_SYNDEF = re.compile(r"\.(" + "|".join(SYNONYM_CLASSES) + r")\s*[,{]")
RE_TEXT_TERTIARY = re.compile(r"var\(--text-tertiary\)")
RE_PRIMARY = re.compile(r"var\(--primary\)")
RE_FORBIDDEN = re.compile(r"\boklch\(|backdrop-filter\s*:")
RE_IMPORTANT = re.compile(r"!important")
RE_IMPORT = re.compile(r"@import\s+[\"']?([^\"';]+)")
RE_PROP_VAR = re.compile(r"^\s*var\(--", re.M)  # 属性名位置出现 var(
RE_VAR_NESTED = re.compile(r"var\(--var\(")  # var 二次包裹
RE_VAR_PROP_SUFFIX = re.compile(r"var\(--[a-z0-9-]+\)-[a-z]")  # var() 后跟 -属性片段


def norm_hex(value: str) -> str | None:
    v = value.strip().lower()
    if not v.startswith("#"):
        return None
    body = v[1:]
    if len(body) in (3, 4):
        body = "".join(c * 2 for c in body)
    if len(body) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", body):
        return None
    return "#" + body


def norm_rgba(value: str) -> str | None:
    v = re.sub(r"\s+", "", value)
    m = re.fullmatch(r"(rgba?)\(([^)]+)\)", v)
    if not m:
        return None
    parts = [p.strip() for p in m.group(2).split(",")]
    if len(parts) not in (3, 4):
        return None
    try:
        nums = [round(float(p)) for p in parts[:3]]
        alpha = round(float(parts[3]), 2) if len(parts) == 4 else 1.0
    except ValueError:
        return None
    return f"{m.group(1)}({','.join(str(n) for n in nums)},{alpha:g})"


def token_values(app_wxss_text: str) -> set[str]:
    """app.wxss page{} 内定义的全部令牌值（hex/rgba 归一化）。"""
    vals: set[str] = set()
    for line in app_wxss_text.splitlines():
        line = strip_comments(line)
        m = re.match(r"\s*--[a-zA-Z0-9-]+\s*:\s*([^;]+);", line)
        if not m:
            continue
        v = m.group(1).strip()
        h = norm_hex(v)
        if h:
            vals.add(h)
        r = norm_rgba(v)
        if r:
            vals.add(r)
    return vals


def load_tokens() -> tuple[set[str], dict[str, str]]:
    text = MINIAPP.joinpath("app.wxss").read_text(encoding="utf-8")
    names: dict[str, str] = {}
    for line in text.splitlines():
        line = strip_comments(line)
        m = re.match(r"\s*(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", line)
        if m:
            names[m.group(1)] = m.group(1).strip()
    return token_values(text), names


def registered_pages() -> list[str]:
    cfg = json.loads(MINIAPP.joinpath("app.json").read_text(encoding="utf-8"))
    pages = list(cfg.get("pages", []))
    for pkg in cfg.get("subPackages", []):
        root = pkg.get("root", "").rstrip("/")
        for p in pkg.get("pages", []):
            pages.append(f"{root}/{p}")
    return pages


def wxss_files() -> list[Path]:
    files = [MINIAPP / "app.wxss"]
    files += sorted((MINIAPP / "pages").rglob("*.wxss"))
    files += sorted((MINIAPP / "components").rglob("*.wxss"))
    files += sorted((MINIAPP / "custom-tab-bar").rglob("*.wxss"))
    return [f for f in files if f.is_file()]


def exempted(rel: str, line: str) -> bool:
    for suffix, pattern, _reason, _date in WHITELIST:
        if rel.endswith(suffix) and re.search(pattern, line):
            return True
    return False


def in_var_fallback(line: str, pos: int) -> bool:
    """pos 处字面量是否落在 var(--x, <字面>) 兜底位。
    E-20260914 教训：custom-tab-bar 等组件拿不到 page 变量，
    必须写 var(--x, #hex) 兜底——这种字面量是正确写法，不算裸色违规。"""
    depth = 0
    for j in range(pos - 1, -1, -1):
        c = line[j]
        if c == ")":
            depth += 1
        elif c == "(":
            if depth == 0:
                if line[max(0, j - 3) : j] == "var":
                    return "," in line[j:pos]
                return False
            depth -= 1
    return False


def scan_colors(app_wxss: Path, wxss: list[Path], tokens: set[str], base: Path = MINIAPP):
    """R1: 返回 [(file, line_no, raw, kind)]，kind∈{'token-equal','true-split','func','named'}。"""
    out: list[tuple[str, int, str, str]] = []
    for f in wxss:
        if f.resolve() == app_wxss.resolve():
            continue
        rel = str(f.relative_to(base))
        text = strip_comments(f.read_text(encoding="utf-8"))
        for i, line in enumerate(text.splitlines(), 1):
            if exempted(rel, line):
                continue
            for m in RE_HEX.finditer(line):
                h = norm_hex(m.group(0))
                if h is None:
                    continue
                if in_var_fallback(line, m.start()):
                    continue  # var(--x, #hex) 字面兜底：组件场景正确写法
                kind = "token-equal" if h in tokens else "true-split"
                out.append((rel, i, m.group(0), kind))
            for m in RE_FUNC_COLOR.finditer(line):
                if "var(" in m.group(0):
                    continue  # rgba(var(--x-rgb), a) 属令牌引用，不算裸值
                if in_var_fallback(line, m.start()):
                    continue  # var(--x, rgba(...)) 字面兜底
                kind = "func"
                r = norm_rgba(m.group(0))
                if r and r in tokens:
                    kind = "token-equal"
                out.append((rel, i, m.group(0), kind))
            # 只扫声明区（{ 之后）：.sc-icon.blue 这类类名不算具名色
            decl_part = line.split("{")[-1]
            for m in RE_NAMED.finditer(decl_part):
                out.append((rel, i, m.group(0), "named"))
    return out


def scan_inline_style(pages: list[str], base: Path = MINIAPP):
    """R2: (hard[(f,l,decl)], warn[(f,l,decl)])。仅扫已注册页面 wxml。"""
    hard, warn = [], []
    for page in pages:
        f = base / (page + ".wxml")
        if not f.is_file():
            continue
        rel = str(f.relative_to(base))
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for sm in RE_STYLE_ATTR.finditer(line):
                val = sm.group(1)
                if "{{" in val:
                    continue
                if exempted(rel, line):
                    continue
                for dm in RE_DECL.finditer(val):
                    prop, pv = dm.group(1).strip(), dm.group(2).strip()
                    entry = (rel, i, f"{prop}:{pv}")
                    if (
                        prop in COLOR_PROPS
                        or RE_HEX.search(pv)
                        or RE_FUNC_COLOR.search(pv)
                        or RE_NAMED.search(pv)
                    ):
                        hard.append(entry)
                    elif prop in SIZE_PROPS:
                        hard.append(entry)
                    elif prop in LAYOUT_PROPS:
                        warn.append(entry)
    return hard, warn


def scan_synonyms(wxss: list[Path], base: Path = MINIAPP):
    """R3: 页面 wxss 中重复定义公共类。"""
    out = []
    for f in wxss:
        rel = str(f.relative_to(base))
        if rel == "app.wxss" or rel.startswith("components/"):
            continue
        text = strip_comments(f.read_text(encoding="utf-8"))
        for i, line in enumerate(text.splitlines(), 1):
            for m in RE_SYNDEF.finditer(line):
                if not exempted(rel, line):
                    out.append((rel, i, m.group(0)))
    return out


def scan_orphans(pages: list[str], base: Path = MINIAPP):
    """R4: pages/ 下未注册且未被 @import 引用的文件。"""
    registered = set(pages)
    imported: set[str] = set()
    for f in base.rglob("*.wxss"):
        for m in RE_IMPORT.finditer(f.read_text(encoding="utf-8")):
            target = m.group(1).strip()
            if target.endswith(".wxss"):
                tgt = (f.parent / target).resolve()
                if tgt.is_file() and base in tgt.parents:
                    imported.add(str(tgt.relative_to(base)))
    # wxml 的 <include src> / <import src> 引用同样算"被引用"
    for f in base.rglob("*.wxml"):
        for m in re.finditer(
            r"<(?:include|import)\s+src=\"([^\"]+)\"", f.read_text(encoding="utf-8")
        ):
            tgt = (f.parent / m.group(1)).resolve()
            if tgt.is_file() and base in tgt.parents:
                imported.add(str(tgt.relative_to(base)))
    out = []
    pages_dir = base / "pages"
    for f in sorted(pages_dir.rglob("*")):
        if f.suffix not in (".wxss", ".wxml", ".js", ".json") or not f.is_file():
            continue
        rel = str(f.relative_to(base))
        stem = f.with_suffix("").relative_to(base).as_posix()
        if stem in registered or rel in imported:
            continue
        out.append(rel)
    return out


def scan_json(pages: list[str], tokens: set[str], base: Path = MINIAPP):
    """R5: JSON 层值域校验 + 页面 json 禁设 navigationBarBackgroundColor。"""
    out = []
    json_files = [base / "app.json"]
    json_files += [base / (p + ".json") for p in pages]
    for f in json_files:
        if not f.is_file():
            continue
        rel = str(f.relative_to(base))
        cfg = json.loads(f.read_text(encoding="utf-8"))

        def walk(node: object, key: str | None = None, rel: str = rel) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, k)
            elif isinstance(node, list):
                for v in node:
                    walk(v, key)
            elif isinstance(node, str):
                if key == "navigationBarBackgroundColor" and rel != "app.json":
                    out.append((rel, 0, "navigationBarBackgroundColor 禁设（保持继承）"))
                    return
                for hm in RE_HEX.finditer(node):
                    h = norm_hex(hm.group(0))
                    if h and h not in tokens:
                        out.append((rel, 0, f"hex {hm.group(0)} 不在令牌值集合"))

        walk(cfg)
    return out


def scan_canonical(wxss: list[Path], base: Path = MINIAPP):
    """R6: canonical 令牌名（--text-tertiary 禁用；--primary WARN）。"""
    hard, warn = [], []
    for f in wxss:
        rel = str(f.relative_to(base))
        for i, line in enumerate(strip_comments(f.read_text(encoding="utf-8")).splitlines(), 1):
            if RE_TEXT_TERTIARY.search(line):
                hard.append((rel, i, "var(--text-tertiary) → 用 var(--muted)"))
            elif RE_PRIMARY.search(line) and rel != "app.wxss":
                warn.append((rel, i, "var(--primary) → 新代码用 var(--accent)"))
    return hard, warn


def scan_forbidden(wxss: list[Path], app_wxss: Path, base: Path = MINIAPP):
    """R7: oklch(/backdrop-filter 全禁；!important 出 app.wxss 即违规。"""
    out = []
    for f in wxss:
        is_app = f.resolve() == app_wxss.resolve()
        rel = str(f.relative_to(base))
        for i, line in enumerate(strip_comments(f.read_text(encoding="utf-8")).splitlines(), 1):
            if RE_FORBIDDEN.search(line):
                out.append((rel, i, m_group(line)))
            if not is_app and RE_IMPORTANT.search(line):
                out.append((rel, i, "!important"))
    return out


def scan_syntax(wxss: list[Path], base: Path = MINIAPP):
    """R8: 属性名/嵌套 var 损坏检测（字符串替换手术的后遗症）。"""
    out = []
    for f in wxss:
        rel = str(f.relative_to(base))
        text = f.read_text(encoding="utf-8")
        if RE_VAR_NESTED.search(text):
            out.append((rel, 0, "var 二次包裹 var(--var("))
        if RE_PROP_VAR.search(text):
            out.append((rel, 0, "属性名位置出现 var(--"))
        for m in RE_VAR_PROP_SUFFIX.finditer(text):
            out.append((rel, 0, f"var() 后跟属性片段: {m.group(0)}"))
    return out


FALLBACK_REQUIRED = ("custom-tab-bar/index.wxss", "components/avatar-ring/avatar-ring.wxss")


def scan_brace_structure(wxss: list[Path], base: Path = MINIAPP):
    """R9: 花括号嵌套结构校验。

    E-20260914 事故教训：count('{')==count('}') 只保证数量相等，
    「缺中间 + 补尾括号」会让数量平衡而结构非法 → 样式编译失败 → 渲染层全灭。
    """
    out = []
    for f in wxss:
        rel = str(f.relative_to(base))
        text = f.read_text(encoding="utf-8")
        depth, line, i, n = 0, 1, 0, len(text)
        while i < n:
            c = text[i]
            if c == "\n":
                line += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                j = text.find("*/", i + 2)
                if j == -1:
                    out.append((rel, line, "注释未闭合"))
                    break
                line += text.count("\n", i, j)
                i = j + 2
                continue
            elif c in "\"'":
                q = c
                j = i + 1
                while j < n and text[j] != q:
                    if text[j] == "\\":
                        j += 2
                        continue
                    j += 1
                line += text.count("\n", i, j)
                i = j + 1
                continue
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth < 0:
                    out.append((rel, line, "多余 '}'（嵌套深度变负）"))
                    depth = 0
            i += 1
        else:
            if depth != 0:
                out.append((rel, 0, f"未闭合 '{{' × {depth}"))
    return out


def scan_component_fallback(wxss: list[Path], base: Path = MINIAPP):
    """R10: 拿不到 page 变量的组件，「var(--x)」必须带字面兜底。

    E-20260914 根因：custom-tab-bar 渲染在页面树之外，app.wxss 的 page{} 变量
    传不进去 → 全部令牌化后背景/贴纸框/红点集体失效（用户投诉「有的透明有的不透明」）。
    正确写法：var(--x, <原字面值>)。
    """
    out = []
    for f in wxss:
        rel = str(f.relative_to(base))
        if rel not in FALLBACK_REQUIRED:
            continue
        for i, line in enumerate(strip_comments(f.read_text(encoding="utf-8")).splitlines(), 1):
            for m in re.finditer(r"var\(--[a-zA-Z0-9-]+\s*\)", line):
                out.append((rel, i, m.group(0)))
    return out


def scan_selector_var(wxss: list[Path], base: Path = MINIAPP):
    """R11: 选择器位置出现 var() —— 非法选择器，会让整个分包 WXSS 编译失败。

    E-20260914-11 事实：批处理把类名里的 `gold`/`silver` 也做了子串替换，
    `.podium-card.gold` → `.podium-card.var(--gold)` → member-pkg 分包样式编译失败
    → **该分包全部页面白屏**（用户投诉"点击没反应/卡死"的真正根因）。
    R8 只查了"属性名位置"，漏了"选择器位置"，故单列一条。
    """
    out = []
    for f in wxss:
        rel = str(f.relative_to(base))
        for i, line in enumerate(strip_comments(f.read_text(encoding="utf-8")).splitlines(), 1):
            if "{" not in line:
                continue
            sel = line.split("{")[0]
            if sel.strip().startswith("@"):
                continue
            if "var(--" in sel:
                out.append((rel, i, line.strip()[:80]))
    return out


def m_group(line: str) -> str:
    m = RE_FORBIDDEN.search(line)
    return m.group(0) if m else "forbidden"


# R12 基线（2026-09-16 首扫）：**已知悬空类名**，第二批待清；只许减不许增。
# 新增（不在本表）直接 FAIL —— 这条规则专治「设计做完、模板没接」：
#   WXML 写 speed-chip-active，WXSS 定义的是 .speed-chip.active → 选中态永远不亮（用户报障）。
# 清理方式二选一：① 把 WXML 类名改成 WXSS 已有的（多数情况）；② 在页面 wxss 补上样式。
R12_BASELINE: dict[str, tuple[str, ...]] = {
    # 2026-09-16 二批清空：首扫 22 个类名 / 35 处引用全部处理完毕
    # （改错类名 4 处、补设计缺失 10 处、换共享组件 1 处、去冗余类 2 处），基线归零。
    # 规则照旧：新增（不在本表）直接 FAIL —— 本表应保持为空，别再往里加。
}


def _class_tokens_from_attr(raw: str) -> set[str]:
    """从 class="..." 抽出**静态可判**的类名（动态值先剔除，防误报）。

    - 去掉 {{...}} 后的字面 token = 静态类名
    - {{cond ? 'a' : 'b'}} 的分支字面量 = 条件类名；`=== 'x'` 里的比较值**不算**类名
    - `badge-{{cond ? 'paid' : 'x'}}` 这种前缀拼接 = 真实类名 badge-paid；
      **拼接过的那段不再单独出裸类名**（否则 paid/pending/refunded 全是假阳性）
    """
    out: set[str] = set()
    # ① 前缀拼接：xxx-{{...}} → 分支拼上前缀才是真实类名
    for pm in re.finditer(r"([A-Za-z][A-Za-z0-9_\-]*-)\{\{(.*?)\}\}", raw, re.S):
        for b in re.finditer(r"'([A-Za-z][A-Za-z0-9_\-]*)'", pm.group(2)):
            out.add(pm.group(1) + b.group(1))
    # ② 拼接过的那段先挖掉，剩下的 {{...}} 才按条件类名解析
    masked = re.sub(r"[A-Za-z][A-Za-z0-9_\-]*-(\{\{.*?\}\})", " ", raw, flags=re.S)
    cleaned = re.sub(r"[=!]==?\s*['\"][^'\"]*['\"]", " ", masked)
    cleaned = re.sub(r"[=!]==?\s*[^?:\s]+", " ", cleaned)
    cleaned = re.sub(r"[<>]=?\s*[^?:\s]+", " ", cleaned)
    for m in re.finditer(r"'([A-Za-z][A-Za-z0-9_\-]*)'|\"([A-Za-z][A-Za-z0-9_\-]*)\"", cleaned):
        tok = m.group(1) or m.group(2)
        if not tok.endswith("-"):
            out.add(tok)
    static = re.sub(r"\{\{.*?\}\}", " ", masked, flags=re.S)
    for t in re.findall(r"[A-Za-z][A-Za-z0-9_\-]*", static):
        if not t.endswith("-"):
            out.add(t)
    return out


def _wxss_scope(css_paths, seen=None) -> str:
    """把一批 wxss 连同 @import 链拼成一坨文本（类名定义都算在内）。"""
    seen = seen if seen is not None else set()
    text = ""
    for p in css_paths:
        p = Path(p)
        if p in seen or not p.exists():
            continue
        seen.add(p)
        raw = p.read_text(encoding="utf-8")
        text += raw
        for imp in re.findall(r"@import\s+[\"']([^\"']+)[\"']", raw):
            text += _wxss_scope([(p.parent / imp).resolve()], seen)
    return text


def scan_undefined_classes(base: Path = MINIAPP):
    """R12: WXML 里用到的类名必须在该页**作用域内**的 wxss 有定义。

    作用域 = 同目录全部 wxss（含 @import 链）+ app.wxss（含 @import 链）。
    WXSS 是**按页隔离**的，别的页面定义了同名类不算（`.page-bg` 就是这么漏的）。
    只判静态可判类名（见 _class_tokens_from_attr），动态值一律不判，防误报。
    """
    new: list[tuple[str, int, str]] = []
    baseline_hits: list[tuple[str, int, str]] = []
    for wxml in sorted(base.rglob("*.wxml")):
        if "__pycache__" in wxml.parts:
            continue
        rel = str(wxml.relative_to(base))
        scope_css: list[Path] = [base / "app.wxss"]
        if "components" in wxml.parts and (wxml.parent / f"{wxml.stem}.wxss").exists():
            scope_css.append(wxml.parent / f"{wxml.stem}.wxss")
        else:
            scope_css += sorted(wxml.parent.glob("*.wxss"))
        defined = set(re.findall(r"\.([A-Za-z][A-Za-z0-9_\-]*)", _wxss_scope(scope_css)))
        text = wxml.read_text(encoding="utf-8")
        allowed = set(R12_BASELINE.get(rel, ()))
        for m in re.finditer(r'class\s*=\s*"([^"]*)"', text):
            ln = text[: m.start()].count("\n") + 1
            for tok in sorted(_class_tokens_from_attr(m.group(1))):
                if tok in defined:
                    continue
                (baseline_hits if tok in allowed else new).append((rel, ln, tok))
    return new, baseline_hits


# R13 图标槽位（2026-09-16）：两件事必须机器看得住
#   a) 图标槽位（class 含 icon/emoji）里**不许出现 emoji**——三端渲染不一致、颜色与令牌无关；
#   b) 引用的图标资产必须真的存在（`/icons/ui/x.png` / `icon-name="x"`）。
r"""图标槽位里的 emoji（class 含 icon|emoji 的元素文本里出现 emoji）= 违规。"""
RE_ICON_SLOT_EMOJI = re.compile(
    r'class="[^"]*\b(?:[a-z-]*icon[a-z-]*|[a-z-]*emoji[a-z-]*)\b[^"]*"[^>]*>\s*'
    r"(?P<txt>[^<]{0,40})"
)
# 排版字形白名单：✓ ✕ ★ ☆ ▶ 等在 2600~27BF 区间，但属**设计系统文字符号**
# （`已打卡 ✓`、环形勾叉、星级），不是平台 emoji——不能一并禁掉（R13a 误报源，已实证）。
TYPO_GLYPHS = "\u2713\u2714\u2715\u2716\u2717\u2718\u2605\u2606\u25b6\u25c0\u25b2\u25bc\u25cf\u25cb\u25a0\u25a1\u25c6\u25c7\u203b"
RE_EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff]")


def scan_icon_emoji(base: Path = MINIAPP):
    """R13a: 图标槽位出现 emoji（含三元表达式里的 emoji 图标）。"""
    out: list[tuple[str, int, str]] = []
    for wxml in sorted(base.rglob("*.wxml")):
        if "__pycache__" in wxml.parts:
            continue
        for i, line in enumerate(wxml.read_text(encoding="utf-8").split("\n"), 1):
            m = RE_ICON_SLOT_EMOJI.search(line)
            if not m:
                continue
            hit = next((c for c in RE_EMOJI.findall(m.group("txt")) if c not in TYPO_GLYPHS), None)
            if hit:
                out.append((str(wxml.relative_to(base)), i, hit))
    return out


def scan_icon_assets(base: Path = MINIAPP):
    """R13b: 图标引用必须落到真实资产（/icons/ui/*.png 与 icon-name）。"""
    have = {p.stem for p in (base / "icons" / "ui").glob("*.png")}
    out: list[tuple[str, int, str]] = []
    for wxml in sorted(base.rglob("*.wxml")):
        if "__pycache__" in wxml.parts:
            continue
        rel = str(wxml.relative_to(base))
        for i, line in enumerate(wxml.read_text(encoding="utf-8").split("\n"), 1):
            for name in re.findall(r'icon-name="([a-z0-9-]+)"', line):
                if name not in have:
                    out.append((rel, i, name))
            for name in re.findall(r"/icons/ui/([a-z0-9-]+)\.png", line):
                if name not in have:
                    out.append((rel, i, name))
            for expr in re.findall(r"/icons/ui/\{\{([^}]*)\}\}\.png", line):
                for name in re.findall(r"'([a-z0-9-]+)'", expr):
                    if name not in have:
                        out.append((rel, i, name))
    return out


def self_test(tokens: set[str]) -> list[str]:
    """S1 注入自检：对内置违例样本运行检测器，必须全部命中。"""
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="miniapp-style-selftest-"))
    try:
        (tmp / "pages").mkdir(parents=True)
        (tmp / "app.wxss").write_text(
            "page { --accent: #FF6B35; --error: #EF4444; }\n", encoding="utf-8"
        )
        (tmp / "pages/demo.wxss").write_text(
            ".a { color: #123456; }\n.b { color: red; }\n"
            ".empty-text { font-size: 28rpx; }\n.oklchx { color: oklch(0.5 0.1 20); }\n"
            ".imp { color: var(--accent) !important; }\n",
            encoding="utf-8",
        )
        (tmp / "pages/broken.wxss").write_text(
            ".c { var(--surface)-space: nowrap; }\n.d { color: var(--var(--x)); }\n",
            encoding="utf-8",
        )
        (tmp / "pages/demo.wxml").write_text(
            '<view style="color: #123456; display: flex;">x</view>\n', encoding="utf-8"
        )
        cfg = {"pages": ["pages/demo"], "subPackages": []}
        (tmp / "app.json").write_text(json.dumps(cfg), encoding="utf-8")
        (tmp / "pages/orphan.wxss").write_text(".z { color: #fff; }\n", encoding="utf-8")
        (tmp / "pages/unbalanced.wxss").write_text(
            ".u { color: var(--accent); }\n}\n", encoding="utf-8"
        )
        (tmp / "pages/selvar.wxss").write_text(
            ".podium-card.var(--gold) { color: red; }\n", encoding="utf-8"
        )
        # R12 注入样本：demo.wxss 只定义了 .a/.b，wxml 里故意用 .ghost（悬空）+ .a（正常）
        # 自证夹具里放一个真实存在的图标资产：同时验证"存在的别误报 / 不存在的必须报"
        (tmp / "icons" / "ui").mkdir(parents=True, exist_ok=True)
        (tmp / "icons" / "ui" / "calendar.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp / "pages/emoji.wxml").write_text(
            '<text class="fi-icon">📅</text>\n'
            '<image class="fi-icon" src="/icons/ui/calendar.png" mode="aspectFit" />\n'
            '<empty-state icon-name="nosuchicon" title="x" />\n',
            encoding="utf-8",
        )
        (tmp / "pages/ghost.wxml").write_text(
            "<view class=\"ghost {{ok ? 'a' : 'missing-cond'}}\">x</view>\n", encoding="utf-8"
        )

        app_wxss = tmp / "app.wxss"
        toks = token_values(app_wxss.read_text(encoding="utf-8"))
        wfiles = [
            tmp / "pages/demo.wxss",
            tmp / "pages/orphan.wxss",
            tmp / "pages/broken.wxss",
            tmp / "pages/unbalanced.wxss",
            tmp / "pages/selvar.wxss",
        ]
        colors = scan_colors(app_wxss, wfiles, toks, base=tmp)
        if len(colors) < 4:
            failures.append(f"S1 色值检测漏检: {colors}")
        hard, warn = scan_inline_style(["pages/demo"], base=tmp)
        if not hard:
            failures.append("S1 内联颜色漏检")
        if not warn:
            failures.append("S1 内联布局 WARN 漏检")
        syn = scan_synonyms(wfiles, base=tmp)
        if not syn:
            failures.append("S1 同义类漏检")
        orphans = scan_orphans(["pages/demo"], base=tmp)
        if "pages/orphan.wxss" not in orphans:
            failures.append(f"S1 孤儿漏检: {orphans}")
        forb = scan_forbidden(wfiles, app_wxss, base=tmp)
        if len(forb) < 2:
            failures.append(f"S1 禁项漏检: {forb}")
        syn8 = scan_syntax(wfiles, base=tmp)
        br = scan_brace_structure(wfiles, base=tmp)
        sv = scan_selector_var(wfiles, base=tmp)
        r12_new, _r12_base = scan_undefined_classes(base=tmp)
        r12_hits = {t for _f, _l, t in r12_new}
        if "ghost" not in r12_hits or "missing-cond" not in r12_hits:
            failures.append(f"S1 悬空类名漏检: {r12_new}")
        if "a" in r12_hits:
            failures.append(f"S1 悬空类名误报（.a 在 demo.wxss 已定义）: {r12_new}")
        r13e = scan_icon_emoji(base=tmp)
        r13a = scan_icon_assets(base=tmp)
        if not r13e:
            failures.append("S1 图标槽位 emoji 漏检")
        if not any(n == "nosuchicon" for _f, _l, n in r13a):
            failures.append(f"S1 图标资产缺失漏检: {r13a}")
        if any(n == "calendar" for _f, _l, n in r13a):
            failures.append("S1 图标资产误报（calendar 存在）")
        if not sv:
            failures.append("S1 非法选择器漏检")
        if not br:
            failures.append("S1 花括号结构失衡漏检")
        if len(syn8) < 2:
            failures.append(f"S1 语法健康漏检: {syn8}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def main() -> int:
    tokens, _names = load_tokens()
    pages = registered_pages()
    wxss = wxss_files()
    app_wxss = MINIAPP / "app.wxss"

    errors: list[str] = []

    # S2 空结果自检
    if not pages:
        errors.append("S2 注册页为 0——app.json 解析失败或扫描范围损坏")
    if len(wxss) == 0:
        errors.append("S2 扫描到 0 个 wxss——扫描范围损坏")
    # S3 基线数字下限
    if len(pages) < MIN_PAGES:
        errors.append(f"S3 注册页 {len(pages)} < {MIN_PAGES}——扫描范围异常")
    if len(wxss) < MIN_WXSS:
        errors.append(f"S3 wxss {len(wxss)} < {MIN_WXSS}——扫描范围异常")
    # S1 注入自检
    st = self_test(tokens)
    errors.extend(st)

    colors = scan_colors(app_wxss, wxss, tokens)
    inline_hard, inline_warn = scan_inline_style(pages)
    syn = scan_synonyms(wxss)
    orphans = scan_orphans(pages)
    json_bad = scan_json(pages, tokens)
    canon_hard, canon_warn = scan_canonical(wxss)
    forb = scan_forbidden(wxss, app_wxss)
    syntax = scan_syntax(wxss)
    braces = scan_brace_structure(wxss)
    nofb = scan_component_fallback(wxss)
    selv = scan_selector_var(wxss)
    undef_new, undef_base = scan_undefined_classes()
    icon_emoji = scan_icon_emoji()
    icon_asset = scan_icon_assets()

    n_token_equal = sum(1 for c in colors if c[3] == "token-equal")
    n_true_split = sum(1 for c in colors if c[3] == "true-split")
    n_func = sum(1 for c in colors if c[3] == "func")
    n_named = sum(1 for c in colors if c[3] == "named")

    print("=" * 64)
    print("check_miniapp_style — 小程序风格基准机械门禁")
    print(f"基线数字: 注册页 {len(pages)} / wxss {len(wxss)} / 令牌值 {len(tokens)} 个")
    print(
        f"R1 裸色值: {len(colors)} 处（值=令牌 {n_token_equal} | "
        f"真割裂 {n_true_split} | 色函数 {n_func} | 具名色 {n_named}）"
    )
    print(f"R2 内联样式: 违规 {len(inline_hard)} / 布局 WARN {len(inline_warn)}")
    print(f"R3 同义类重复定义: {len(syn)}")
    print(f"R4 孤儿文件: {len(orphans)}")
    print(f"R5 JSON 值域/禁设: {len(json_bad)}")
    print(f"R6 规范令牌名: 违规 {len(canon_hard)} / WARN {len(canon_warn)}")
    print(f"R7 禁项: {len(forb)}")
    print(f"R8 样式语法: {len(syntax)}")
    print(f"R9 花括号结构: {len(braces)}")
    print(f"R10 组件变量兜底: {len(nofb)}")
    print(f"R11 非法选择器(选择器位置 var): {len(selv)}")
    print(f"R13 图标槽位 emoji: {len(icon_emoji)} / 图标资产缺失: {len(icon_asset)}")
    n_base = sum(len(v) for v in R12_BASELINE.values())
    print(
        f"R12 悬空类名(WXML 用到、本页 wxss 无定义): 新违规 {len(undef_new)} / "
        f"基线待清 {n_base} 个类名（命中 {len(undef_base)} 处引用，只许减）"
    )
    print(f"豁免白名单: {len(WHITELIST)} 条（初始 0 条，遇一例议一例）")
    for suffix, pattern, reason, date in WHITELIST:
        print(f"  - {suffix} /{pattern}/ {reason} ({date})")

    def dump(title: str, rows, limit: int = 200) -> None:
        if not rows:
            return
        print(f"--- {title}（前 {min(len(rows), limit)} 条）---")
        for r in rows[:limit]:
            print("  ", r)

    dump(
        "R1 真割裂（值不在令牌集，优先修）",
        [(f, ln, v, k) for f, ln, v, k in colors if k == "true-split"],
    )
    dump(
        "R1 仅需令牌化（值=令牌）", [(f, ln, v, k) for f, ln, v, k in colors if k == "token-equal"]
    )
    dump("R1 色函数/具名色", [(f, ln, v, k) for f, ln, v, k in colors if k in ("func", "named")])
    dump("R2 内联颜色/尺寸违规", inline_hard)
    dump("R2 内联布局 WARN", inline_warn)
    dump("R3 同义类", syn)
    dump("R4 孤儿文件", orphans)
    dump("R5 JSON", json_bad)
    dump("R6 违规", canon_hard)
    dump("R6 WARN", canon_warn)
    dump("R7 禁项", forb)
    dump("R8 样式语法", syntax)
    dump("R9 花括号结构", braces)
    dump("R10 组件缺字面兜底", nofb)
    dump("R11 非法选择器", selv)
    dump("R12 悬空类名（新违规，必须修）", undef_new)
    dump("R13a 图标槽位 emoji（必须换 /icons/ui 资产）", icon_emoji)
    dump("R13b 图标资产缺失", icon_asset)

    for f, ln, d in braces:
        errors.append(f"R9 {f}:{ln} 结构非法 {d}")
    for f, ln, d in selv:
        errors.append(f"R11 {f}:{ln} 非法选择器（选择器位置出现 var）: {d}")
    for f, ln, d in undef_new:
        errors.append(f"R12 {f}:{ln} 悬空类名 .{d}（WXML 用到但本页 wxss 无定义）")
    for f, ln, ch in icon_emoji:
        errors.append(f"R13a {f}:{ln} 图标槽位里是 emoji {ch!r}（改用 /icons/ui/*.png）")
    for f, ln, name in icon_asset:
        errors.append(f"R13b {f}:{ln} 图标资产不存在: {name}")
    for f, ln, d in nofb:
        errors.append(f"R10 {f}:{ln} 组件变量缺字面兜底 {d}")
    for f, ln, v, _k in colors:
        errors.append(f"R1 {f}:{ln} 裸色值 {v}")
    for f, ln, d in inline_hard:
        errors.append(f"R2 {f}:{ln} 内联样式 {d}")
    for f, ln, d in syn:
        errors.append(f"R3 {f}:{ln} 同义类 {d}")
    for f in orphans:
        errors.append(f"R4 孤儿文件 {f}")
    for f, _l, d in json_bad:
        errors.append(f"R5 {f} {d}")
    for f, ln, d in canon_hard:
        errors.append(f"R6 {f}:{ln} {d}")
    for f, ln, d in forb:
        errors.append(f"R7 {f}:{ln} {d}")
    for f, _l, d in syntax:
        errors.append(f"R8 {f} {d}")
    errors.extend(st)

    print("=" * 64)
    print("⚠ 本检查器不能替代目视：遮挡/覆盖/错位仍需截图证据。")
    if errors:
        print(f"FAIL: {len(errors)} 项违规")
        return 1
    print("PASS: 风格基准全零")
    return 0


if __name__ == "__main__":
    sys.exit(main())
