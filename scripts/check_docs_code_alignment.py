#!/usr/bin/env python
"""文档↔代码一致性检查（E-20260912-05：文档引用悬空族防呆）。

为什么需要它：文档里写了不存在的**文件路径 / 接口路径 / 配置键 / 函数名**，
靠人眼审不出来，但会让下任"照着手册跑"直接卡住。两次实证：
  - 「手册写了不存在的账号 13800000001」→ 按手册走第一步就失败；
  - 「手册让人跑 `scripts/gen_fix33_thumb_compare.py`」→ 该脚本根本没入库。
本脚本把这类断言机器化，接进 gate 第 [5] 步（与 check_miniapp_bindings 并列）。

数据源全部**离线可复现**（CI 无活服务/无 seed 库也能跑）：
  - 接口路径  ← `docs/api/openapi.json`（T27 快照，gate 第 [8] 步保证其与代码同步）
  - 配置键    ← `backend/seeds/seed_configs.py`（配置目录唯一来源）
  - 表名      ← `backend/**/models.py` 的 `__tablename__`
  - 代码符号  ← `backend/**/*.py` + `scripts/*.py` 的 def/class + 前端 function/箭头函数名
  - 文件路径  ← 文件系统

**刻意提及**（如错误记忆库在记录"某个名字是编造的"）用忽略标记豁免：
    <!-- docs-code-ignore: token_a,token_b -->
标记可放在文件任意位置，作用于该文件全文；历史归档 `docs/legacy-attic/` 整体跳过。

退出码：发现断链 → 1（gate 硬失败）；否则 0。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOC_GLOBS = ["docs/**/*.md", "PRD/**/*.md", "error_list/**/*.md"]
DOC_FILES = ["CLAUDE.md", "AGENTS.md"]
SKIP_PARTS = ("legacy-attic",)

# 反引号里可能出现的"非本项目符号"（标准库/ORM/CSS/前端内置）——不算断链
EXTERNAL_SYMBOLS = {
    "int",
    "str",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "len",
    "sum",
    "min",
    "max",
    "abs",
    "round",
    "print",
    "format",
    "split",
    "join",
    "strip",
    "replace",
    "startswith",
    "endswith",
    "lower",
    "upper",
    "append",
    "extend",
    "keys",
    "values",
    "items",
    "get",
    "pop",
    "update",
    "copy",
    "sorted",
    "reversed",
    "enumerate",
    "zip",
    "range",
    "isinstance",
    "hasattr",
    "getattr",
    "setattr",
    "json",
    "dumps",
    "loads",
    "encode",
    "decode",
    "isoformat",
    "strftime",
    "now",
    "today",
    "filter",
    "all",
    "first",
    "count",
    "scalar",
    "flush",
    "commit",
    "rollback",
    "refresh",
    "expire",
    "query",
    "add",
    "delete",
    "merge",
    "with_for_update",
    "populate_existing",
    "group_by",
    "order_by",
    "limit",
    "offset",
    "distinct",
    "outerjoin",
    "func",
    "cast",
    "oklch",
    "var",
    "calc",
    "translate",
    "resolve",
    "url_for",
    "preventDefault",
    "toLocaleString",
    "parseInt",
    "parseFloat",
    "forEach",
    "map",
    "reduce",
    "find",
    "some",
    "every",
    "setData",
    "getTabBar",
    "stopPropagation",
    "requestAnimationFrame",
    "addEventListener",
    "removeEventListener",
    "dispatchEvent",
    "setTimeout",
    "setInterval",
    "clearTimeout",
    "hasPermission",
    "useState",
    "useEffect",
    "useMemo",
    "useCallback",
    "useRef",
    "message",
    "modal",
    "confirm",
    "warn",
    "error",
    "success",
    "info",
    "loading",
    "navigate",
    "redirectTo",
}

# 反引号里带这些形态的一律不校验（占位/通配/示例/正则）
SKIP_TOKEN_RE = re.compile(r"[*|<>]|xxx|\.\.\.|^\d+$", re.I)

PATH_RE = re.compile(
    r"^(backend|admin-web|miniapp|scripts|tests|features|alembic|docs|PRD|error_list|uploads"
    r"|外部专家意见)/[\w./\-{}$]+\.\w+$"
)
API_RE = re.compile(r"^/api/[\w/{}\-]+$")
CONFIG_CTX_RE = re.compile(r"配置键|SystemConfig|开关|阈值|config_key")
TABLE_SUFFIX = (
    "_s",
    "_records",
    "_requests",
    "_states",
    "_ledgers",
    "_snapshots",
    "_questions",
    "_enrollments",
    "_awards",
    "_items",
    "_logs",
    "_configs",
)


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def collect_docs() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for g in DOC_GLOBS:
        out += sorted(ROOT.glob(g))
    for f in DOC_FILES:
        p = ROOT / f
        if p.exists():
            out.append(p)
    return [p for p in out if not any(s in p.parts for s in SKIP_PARTS)]


def collect_api_paths() -> set[tuple[str, ...]]:
    snap = ROOT / "docs/api/openapi.json"
    if not snap.exists():
        print("[docs-code] ⚠ 找不到 docs/api/openapi.json，跳过接口路径校验")
        return set()
    spec = json.loads(_read(snap))
    out = set()
    for p in spec.get("paths", {}):
        segs = tuple(s for s in p.split("/") if s)
        out.add(tuple("{}" if s.startswith("{") else s for s in segs))
    return out


def collect_config_keys() -> set[str]:
    f = ROOT / "backend/seeds/seed_configs.py"
    if not f.exists():
        return set()
    return set(re.findall(r"""["']([a-z][a-z0-9_]{3,40})["']""", _read(f)))


def collect_task_keys() -> set[str]:
    """定时任务键（registry 里的字符串键——文档常按键名引用，不是配置键）。"""
    f = ROOT / "backend/tasks/registry.py"
    if not f.exists():
        return set()
    return set(re.findall(r"""["']([a-z][a-z0-9_]{3,40})["']\s*:\s*TaskSpec""", _read(f)))


def collect_table_names() -> set[str]:
    out = set()
    for f in (ROOT / "backend").rglob("models.py"):
        out |= set(re.findall(r"""__tablename__\s*=\s*["']([a-z_]+)["']""", _read(f)))
    return out


def collect_symbols() -> set[str]:
    out = set()
    for f in list((ROOT / "backend").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        out |= set(re.findall(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", _read(f), re.M))
    for pat, exts in (
        (r"(?:^|\s)(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", (".js", ".ts", ".tsx")),
        (
            r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
            (".js", ".ts", ".tsx"),
        ),
        (r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", (".js", ".ts", ".tsx")),
    ):
        for f in list((ROOT / "miniapp").rglob("*")) + list((ROOT / "admin-web/src").rglob("*")):
            if f.suffix in exts and f.is_file():
                out |= set(re.findall(pat, _read(f), re.M))
    return out


def main() -> int:
    api = collect_api_paths()
    configs = collect_config_keys()
    tables = collect_table_names()
    symbols = collect_symbols() | collect_task_keys()

    findings: list[tuple[str, int, str, str]] = []
    for doc in collect_docs():
        text = _read(doc)
        ignored: set[str] = set()
        for m in re.finditer(r"<!--\s*docs-code-ignore:\s*([^>]+?)\s*-->", text):
            ignored |= {t.strip() for t in m.group(1).split(",") if t.strip()}
        for ln, line in enumerate(text.split("\n"), 1):
            for m in re.finditer(r"`([^`\n]{2,120})`", line):
                tok = m.group(1).strip()
                if not tok or tok in ignored or SKIP_TOKEN_RE.search(tok):
                    continue
                rel = str(doc.relative_to(ROOT))
                # ① 文件路径
                if PATH_RE.match(tok):
                    glob = re.sub(r"\{[^}]*\}", "*", tok)
                    if not list(ROOT.glob(glob)):
                        findings.append((rel, ln, "文件不存在", tok))
                # ② 接口路径
                elif API_RE.match(tok) and api:
                    segs = tuple(
                        "{}" if s.startswith("{") else s for s in tok.split("?")[0].split("/") if s
                    )
                    if len(segs) == 0 or segs not in api:
                        findings.append((rel, ln, "接口路径不存在", tok))
                # ③ 配置键（仅配置语境行，避免把字段名误判）
                elif CONFIG_CTX_RE.search(line) and re.fullmatch(
                    r"[a-z][a-z0-9]*(?:_[a-z0-9]+){1,4}", tok
                ):
                    if tok not in configs and tok not in symbols and tok not in tables:
                        findings.append((rel, ln, "配置键不在目录", tok))
                # ④ 表名
                elif tok.endswith(TABLE_SUFFIX) and re.fullmatch(r"[a-z][a-z0-9_]+", tok):
                    if tok not in tables and tok not in configs and tok not in symbols:
                        findings.append((rel, ln, "表名不存在", tok))
                # ⑤ 函数/类名
                elif tok.endswith("()") and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\)", tok):
                    name = tok[:-2]
                    if name not in symbols and name not in EXTERNAL_SYMBOLS:
                        findings.append((rel, ln, "函数/类不存在", tok))

    print(
        f"[docs-code] 文档 {len(collect_docs())} 份 | 接口 {len(api)} 条 | "
        f"配置键 {len(configs)} 个 | 表 {len(tables)} 张 | 符号 {len(symbols)} 个"
    )
    if not findings:
        print("[docs-code] ✓ 文档引用全部落到真实文件/接口/配置键/符号")
        return 0
    print(f"[docs-code] ✗ 发现 {len(findings)} 处文档引用悬空：")
    for rel, ln, kind, tok in findings:
        print(f"  {rel}:{ln}  [{kind}] {tok}")
    print("  → 改准引用，或对「刻意提及」的 token 加 docs-code-ignore 注释豁免")
    return 1


if __name__ == "__main__":
    sys.exit(main())
