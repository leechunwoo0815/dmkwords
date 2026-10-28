#!/usr/bin/env python
# scripts/check_admin_failure_states.py — 管理端「失败不许伪装成空/0」（第九轮 D 批 N-6）
"""**对等检查器**：小程序侧早有 `check_miniapp_wiring` 的 W4（失败吞成空），管理端一直没人管。

背景（第九轮审查 P2-11 / N-6）：管理端多个列表页把取数失败只弹一次 toast，表格随即显示
「暂无数据」——运营读到的是**语义结论**（"没有这些数据"），而真相是接口挂了。
2026-10-24 D 批实测：首轮扫描在 19 个"有表格 + 取数"的文件里发现 **5 个**仍只有 toast
（`SystemConfig` / `ActivityManage` / `DeadLetterPanel` / `TaskBoard` / `MemberManage`），
其中前四个已在本批补齐"失败提示条 + 重试"；`MemberManage` 一页 3 个独立列表 + 2 个下拉，
各自失败态需分别设计 → **登记挂账**（见 `docs/整改任务书-第九轮-20261022.md` §五 D6）。

两条规则：
  **A1 失败吞成空**：`.catch(() => [])` / `null` / `undefined` / `{}` / `setX([])` ——
      把失败**直接变成空值**，连提示都没有。允许同行或上一行写 `// A1-ok: <理由>` 登记
      （例如"降级为 config_key 原文，不构成假 0"这类已论证的兜底）。
  **A2 列表页必须有失败态**：含 `<Table` 且文件内直接调 `apiXxx(` 的文件，必须出现
      **错误态 state 声明**（`const [xxxError | xxxFailed, setXxx] = useState(...)`）。
      2016-10-24 收紧前它接受任意 `type="warning"`——实测**假阴性**：`Notifications`
      的「截断提示条」和 `PaymentReconcilePanel` 的「差异条数」都被当成失败态，
      而这两个页面的两个列表实际只有 toast（已在本批补齐）。
      确有理由不补的，写文件级 `// A2-ok: <理由>` 登记（会随文件残留而保留）。
      ⚠️ **仍有的局限（如实说明）**：A2 是**文件级**判据——一个文件里有多个独立列表时，
      只要存在**一个**错误态 state 就通过。逐表格化需按 JSX 结构解析，留待单独批；
      多列表页（`MemberManage` 3 个 / `Notifications` 2 个 / `PaymentReconcilePanel` 2 个）
      本轮已**逐个补齐并浏览器实测**，不以机器判据冒充。

自证：
  · S1 注入自检：A1 三种吞法必须命中、`A1-ok` 登记与注释行不得误报；
    A2 缺失败态必须命中、有失败态不得误报；
  · S2 空扫描自检：扫到 0 个 tsx = FAIL（防路径写错 → 全绿）。

用法：`python scripts/check_admin_failure_states.py [--self-test]`
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "admin-web" / "src"

#: A1：catch 直接把失败变成空值（连提示都不给）。
#: 2026-10-28（B4/W8-D2）：原正则只看**单行**、只认无参 `()`，四种写法全漏：
#: `.catch((e) => [])`、块体 `.catch(() => { setX([]); })`、
#: 对象字面量 `.catch(() => ({ ... }))`、跨行 `.catch(() =>\n [])`。
#: 现改为「去注释后全文扫 + 花括号配平块体」，四种形态同一判据。
CATCH_HEAD_RE = re.compile(
    r"\.catch\(\s*(?:\(\s*[^)]*\)|[A-Za-z_$][\w$]*)\s*=>|\.catch\(\s*function\s*\([^)]*\)"
)
#: 空值形态（表达式体）：[] / null / undefined / {} / ({})
EMPTY_VALUE_RE = re.compile(r"^(?:\[\s*\]|null|undefined|\{\s*\})$")
#: 表达式体里直接 `setX([])`（等价于块体里的赋值）
SINGLE_EMPTY_ASSIGN_RE = re.compile(
    r"^set[A-Za-z0-9_$]+\(\s*(?:\[\s*\]|null|undefined|\{\s*\})\s*\)$"
)
#: 块体里的吞空赋值 / 返回
EMPTY_ASSIGN_RE = re.compile(
    r"\bset[A-Za-z0-9_$]+\(\s*(?:\[\s*\]|null|undefined|\{\s*\})\s*\)"
    r"|\breturn\s+(?:\[\s*\]|null|undefined|\{\s*\})\s*;?"
)
#: 块体/对象里的失败提示（任一出现即不算静默吞错）
FAILURE_MARK_RE = re.compile(
    r"message\.|notification\.|console\.(?:error|warn)|toast|alert\("
    r"|set[A-Za-z0-9_$]*(?:Error|Failed)|(?:error|message|failed)\s*:"
)
#: A1 登记逃生口（同行或上一行）
A1_OK_RE = re.compile(r"//\s*A1-ok\s*[:：]\s*\S+")
#: A2：失败态**必须是错误态 state 声明**（2026-10-24 收紧，见 docstring）
ERROR_STATE_DECL_RE = re.compile(
    r"const\s*\[\s*\w*(?:Error|Failed)\w*\s*,\s*set\w+\s*\]\s*=\s*useState"
)
#: A2 文件级逃生口（写入理由）
A2_OK_RE = re.compile(r"//\s*A2-ok\s*[:：]\s*\S+")
TABLE_RE = re.compile(r"<Table\b")
API_CALL_RE = re.compile(r"\bapi[A-Z]\w+\(")


def _mask_comments(text: str) -> str:
    """注释字符替换为空格（保留换行与长度），避免注释里的反例被当成代码。"""
    out = list(text)
    i, n = 0, len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
                continue
            if nxt == "*":
                out[i] = out[i + 1] = " "
                i += 2
                while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                    if text[i] != "\n":
                        out[i] = " "
                    i += 1
                if i < n:
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                continue
        i += 1
    return "".join(out)


def _brace_body(text: str, open_index: int) -> str:
    """从 `open_index` 的 `{` 起配平取块体（跳过引号内的括号）。"""
    depth = 0
    i = open_index
    quote = ""
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : i + 1]
        i += 1
    return text[open_index:]


def _catch_line_problems(text: str) -> list[tuple[int, str]]:
    """A1 全文扫描：返回 `(行号, 形态)`。"""
    masked = _mask_comments(text)
    hits: list[tuple[int, str]] = []
    for m in CATCH_HEAD_RE.finditer(masked):
        i = m.end()
        while i < len(masked) and masked[i].isspace():
            i += 1
        if i < len(masked) and masked[i] == "{":
            body = _brace_body(masked, i)
            if EMPTY_ASSIGN_RE.search(body) and not FAILURE_MARK_RE.search(body):
                hits.append((masked[: m.start()].count("\n") + 1, "块体"))
            continue
        # 表达式体：取到分号/换行（上限 240 字符，防跨函数误伤）
        j, quote, depth = i, "", 0
        while j < len(masked) and j - i < 240:
            ch = masked[j]
            if quote:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    quote = ""
            elif ch in "'\"`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            elif ch == "\n" and depth == 0:
                break
            j += 1
        expr = masked[i:j].strip()
        simple = expr.strip("()").strip()
        if (
            EMPTY_VALUE_RE.match(simple)
            or SINGLE_EMPTY_ASSIGN_RE.match(expr)
            or SINGLE_EMPTY_ASSIGN_RE.match(simple)
        ):
            hits.append((masked[: m.start()].count("\n") + 1, "表达式"))
            continue
        # 对象字面量：({ ... }) / { ... }——没有 error/message/failed 键 = 默认值兜底
        if simple.startswith("{") and simple.endswith("}"):
            inner = simple[1:-1]
            if "{" not in inner and not FAILURE_MARK_RE.search(inner):
                hits.append((masked[: m.start()].count("\n") + 1, "对象字面量"))
    return hits


def scan_file(text: str) -> list[str]:
    """单个 tsx/ts 文件的两条规则（自证与主流程共用同一实现）。"""
    problems: list[str] = []
    lines = text.splitlines()
    for lineno, form in _catch_line_problems(text):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        prev = lines[lineno - 2] if lineno >= 2 else ""
        if A1_OK_RE.search(line) or A1_OK_RE.search(prev):
            continue
        problems.append(f"A1 失败吞成空（{form}）：{line.strip()[:100]}")
    if (
        TABLE_RE.search(text)
        and API_CALL_RE.search(text)
        and not ERROR_STATE_DECL_RE.search(text)
        and not A2_OK_RE.search(text)
    ):
        problems.append("A2 列表页没有错误态 state（const [xxxError|Failed, setXxx] = useState）")
    return problems


def scan(root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    files = sorted(root.rglob("*.tsx"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for p in scan_file(text):
            problems.append(f"{path.relative_to(root)}: {p}")
    if not files:
        problems.append("扫描面为空（admin-web/src 下没有 tsx）——路径写错会导致假绿")
    return problems


def self_test() -> int:
    """S1/S2 注入自检：违例必须命中、合法写法零误报。"""
    bad = 0
    sample = "apiX().catch(() => []);\n"
    if not scan_file(sample):
        print("✗ A1 未命中：`.catch(() => [])`")
        bad += 1
    if not scan_file("apiX().catch(() => setRows([]));\n"):
        print("✗ A1 未命中：`.catch(() => setRows([]))`")
        bad += 1
    # W8-D2（2026-10-28）：四种原先漏掉的写法，现在都必须命中
    for sample_text, label in (
        ("apiX().catch((e) => []);\n", "`.catch((e) => [])`"),
        ("apiX().catch(() => { setRows([]); });\n", "块体 `.catch(() => { setRows([]); })`"),
        ("apiX().catch(() => ({ rows: [] }));\n", "对象字面量 `.catch(() => ({ rows: [] }))`"),
        ("apiX().catch(() =>\n  []);\n", "跨行 `.catch(() =>\\n [])`"),
    ):
        if not scan_file(sample_text):
            print(f"✗ A1 未命中：{label}")
            bad += 1
    for sample_text in (
        "apiX().catch(() => { setRows([]); message.error('加载失败'); });\n",
        "apiX().catch(() => ({ rows: [], error: '加载失败' }));\n",
        "apiX().catch((e) => { console.error(e); setRows([]); });\n",
    ):
        if scan_file(sample_text):
            print(f"✗ A1 误报：有失败提示的兜底被拦：{sample_text.strip()}")
            bad += 1
    if scan_file("apiX().catch(() => []); // A1-ok: 下拉兜底\n"):
        print("✗ A1 误报：已登记 A1-ok 的行被拦")
        bad += 1
    if scan_file("// 文档：旧写法 .catch(() => {}) 已废弃\n"):
        print("✗ A1 误报：注释里的反例被拦")
        bad += 1
    if scan_file("apiX().catch((e) => setListError(e.message));\n"):
        print("✗ A1 误报：正常失败态被拦")
        bad += 1
    # A2：有表 + 取数 + 无失败态 → 必须命中
    if not scan_file("<Table />\napiListX().then(setRows);\n"):
        print("✗ A2 未命中：列表页无失败态")
        bad += 1
    if scan_file(
        "<Table />\napiListX().then(setRows);\nconst [listError, setListError] = useState('');\n"
    ):
        print("✗ A2 误报：有失败态的文件被拦")
        bad += 1
    if scan_file("<Table />\n// 纯展示表格，数据由 props 传入\n"):
        print("✗ A2 误报：无文件内取数的展示表格被拦")
        bad += 1
    # 2026-10-24 收紧回归：只有业务警示（type="warning"）但无错误态 state → 必须命中
    if not scan_file(
        '<Table />\napiListX().then(setRows);\n<Typography type="warning">差异 3 条</Typography>\n'
    ):
        print('✗ A2 未命中：只有 type="warning" 业务警示、无错误态 state')
        bad += 1
    if scan_file("<Table />\napiListX().then(setRows);\n// A2-ok: 表格数据由父组件注入\n"):
        print("✗ A2 误报：已登记 A2-ok 的文件被拦")
        bad += 1
    # S2 空扫描
    with tempfile.TemporaryDirectory(prefix="admin-failure-selftest-") as d:
        if not scan(pathlib.Path(d)):
            print("✗ S2 空扫描未判红")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print(
        "✓ S1 注入自检通过（A1 五种吞法命中（含参数/块体/对象/跨行） + A1-ok/注释/正常失败态零误报"
        " + A2 命中与两条正例零误报）"
    )
    return 0


def main() -> int:
    problems = scan(SRC)
    if problems:
        print(f"FOUND {len(problems)} 处失败态问题：")
        for p in problems:
            print("  ✗", p)
        print(
            "\nFAIL: 管理端失败不许伪装成空/0——列表页补「失败提示条 + 重试」，"
            "确有理由的静默兜底写 `// A1-ok: <理由>` 登记"
        )
        return 1
    print("PASS: 管理端失败态检查通过（A1 无未登记的吞空 catch / A2 列表页均有失败态）✓")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
