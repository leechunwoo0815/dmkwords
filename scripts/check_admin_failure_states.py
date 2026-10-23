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
      `*Error` 失败态 state（或 `type="warning"` / `type="error"` 提示条）。
      ⚠️ **已知局限（如实说明）**：A2 是**文件级**判据——一个文件里有多个独立列表时，
      只要其中一个有失败态就通过。多列表页面（当前只有 `MemberManage`）按挂账处理，
      由人工验收 S258 覆盖，不以机器判据冒充。

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

#: A1：catch 直接把失败变成空值（连提示都不给）
THROWAWAY_CATCH_RE = re.compile(
    r"\.catch\(\s*(?:\(\s*\)|\w+)\s*=>\s*(\[\]|null|undefined|\{\s*\}|set\w+\(\[\]\))\s*\)"
)
#: A1 登记逃生口（同行或上一行）
A1_OK_RE = re.compile(r"//\s*A1-ok\s*[:：]\s*\S+")
#: A2：文件级失败态标记（失败态 state 名 或 提示条 Alert/Text）
FAILURE_STATE_RE = re.compile(r"\w*(?:Error|Failed)\b|type=\"(?:warning|error)\"")
TABLE_RE = re.compile(r"<Table\b")
API_CALL_RE = re.compile(r"\bapi[A-Z]\w+\(")


def scan_file(text: str) -> list[str]:
    """单个 tsx/ts 文件的两条规则（自证与主流程共用同一实现）。"""
    problems: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 跳过纯注释行（文档里写反例是正常的：本仓 DashboardCharts 的注释就在讲旧 `.catch(() => {})`）
        if stripped.startswith(("//", "*", "/*")):
            continue
        m = THROWAWAY_CATCH_RE.search(line)
        if not m:
            continue
        prev = lines[i - 2] if i >= 2 else ""
        if A1_OK_RE.search(line) or A1_OK_RE.search(prev):
            continue
        problems.append(f"A1 失败吞成空：{stripped[:100]}")
    if TABLE_RE.search(text) and API_CALL_RE.search(text) and not FAILURE_STATE_RE.search(text):
        problems.append("A2 列表页没有任何失败态（*Error state / warning 提示条）")
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
    # S2 空扫描
    with tempfile.TemporaryDirectory(prefix="admin-failure-selftest-") as d:
        if not scan(pathlib.Path(d)):
            print("✗ S2 空扫描未判红")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print(
        "✓ S1 注入自检通过（A1 两种吞法命中 + A1-ok/注释/正常失败态零误报 + A2 命中与两条正例零误报）"
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
