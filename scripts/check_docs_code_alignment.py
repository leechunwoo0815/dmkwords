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

# 本地生成产物前缀：**不保证存在于 CI / 新克隆**（uploads 被 gitignore、gate-runs 是本地产物），
# 因此不纳入"文件必须存在"的校验——否则 CI 必红（E-20260912-05 实证：本地绿/CI 红首例）。
# 需要强制入库的是"源码与文档类引用"，见上行 PATH_RE 的白名单前缀。
ARTIFACT_PREFIXES = ("uploads/", "gate-runs/", ".dev-logs/", "backups/")

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
    """配置目录唯一来源：`CONFIG_SEEDS`（元组首元素）。

    ⚠️ 不要用正则扫该文件——它是元组列表，正则会把非键字符串也数进来
    （2026-09-12 实证：正则得 45，实际 40，一度让本检查器误报文档"配置键数不符"）。"""
    sys.path.insert(0, str(ROOT))
    try:
        from backend.seeds.seed_configs import CONFIG_SEEDS

        return {row[0] for row in CONFIG_SEEDS}
    except Exception as exc:  # pragma: no cover - CI 依赖缺失时降级为正则
        print(f"[docs-code] ⚠ CONFIG_SEEDS 导入失败（{type(exc).__name__}），降级正则取键")
        f = ROOT / "backend/seeds/seed_configs.py"
        return (
            set(re.findall(r'^\s{4,8}"([a-z][a-z0-9_]{3,40})",', _read(f), re.M))
            if f.exists()
            else set()
        )


def collect_task_keys() -> set[str]:
    """定时任务键（registry 里的字符串键——文档常按键名引用，不是配置键）。"""
    f = ROOT / "backend/tasks/registry.py"
    if not f.exists():
        return set()
    return set(re.findall(r"""["']([a-z][a-z0-9_]{3,40})["']\s*:\s*TaskSpec""", _read(f)))


def collect_table_names() -> set[str]:
    out = set()
    for f in (ROOT / "backend").rglob("*.py"):  # 含 common/system_models.py 等非 models.py 命名
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


ARCHIVE_HEADING_RE = re.compile(r"^##\s*v\d+·")

# 数量断言只在"当前规格类"文档校验：这些文档的数字声称描述**当前事实**。
# 其余文档（docs/01 阶段表 / LEDGER 时序行 / PRD 签署稿+历史注 / error_list 叙事）
# 的数字属于"当时或阶段叙述"，机器无从判断时点 → 不校验（宪法 §〇.3 亦要求文档少手抄数字）。
QUANTITY_CHECKED = (
    "CLAUDE.md",  # 宪法：当前法律
    "docs/02",
    "docs/03",
    "docs/04",
    "docs/18",  # 当前规格类
    "docs/项目交接-",  # 交接卡：只查当前层（归档层由 ARCHIVE_HEADING_RE 截断）
)
# 不查：docs/01（阶段规划表，数字是里程碑时点快照）、LEDGER（时序行）、PRD（签署稿+历史注）、error_list（叙事）
# 历史对比表述（"旧 7 域"/"原 12 项"）不校验
HISTORICAL_PREFIX_RE = re.compile(r"(旧|原|早期|曾|previous)")


def check_quantity_claims(
    doc: pathlib.Path, text: str, actual: dict[str, int]
) -> list[tuple[int, str, str]]:
    """数量类断言校验（"任务数 N / 配置键 N / N 张表 / N 域"）。

    边界规则：交接卡类文件的历史归档层（首个 `## vNN·` 标题起）**是当时真值**，
    机器无法判断其属于哪个时点，故一律跳过；只校验"当前层"的数字。"""
    out: list[tuple[int, str, str]] = []
    for ln, line in enumerate(text.split("\n"), 1):
        if ARCHIVE_HEADING_RE.match(line):
            break
        for pat, key in (
            (r"(?:任务数|定时任务)\s*\**\s*(\d+)", "任务"),
            (r"配置键\s*\**\s*(\d+)", "配置键"),
            (r"(\d+)\s*张表", "表"),
            (r"(\d+)\s*域", "域"),
        ):
            for m in re.finditer(pat, line):
                got = int(m.group(1))
                if HISTORICAL_PREFIX_RE.search(line[: m.start(1)]):
                    continue  # 历史对比表述（"vs 旧 7 域"）非当前事实
                if got != actual[key]:
                    out.append((ln, f"{key}数不符", f"文档写 {got}，实际 {actual[key]}"))
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
                if tok.startswith(ARTIFACT_PREFIXES):
                    continue  # 产物类引用：本地生成，CI 不保证存在（见 ARTIFACT_PREFIXES 注释）
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

    actual = {
        "任务": len(collect_task_keys()),
        "配置键": len(configs),
        "表": len(tables),
        "域": len([p for p in (ROOT / "backend/domain").iterdir() if p.is_dir()]),
    }
    for doc in collect_docs():
        rel = str(doc.relative_to(ROOT))
        if not rel.startswith(QUANTITY_CHECKED):
            continue
        for ln, kind, detail in check_quantity_claims(doc, _read(doc), actual):
            findings.append((str(doc.relative_to(ROOT)), ln, kind, detail))

    print(
        f"[docs-code] 文档 {len(collect_docs())} 份 | 接口 {len(api)} 条 | "
        f"配置键 {len(configs)} 个 | 表 {len(tables)} 张 | 符号 {len(symbols)} 个 | "
        f"任务 {actual['任务']} | 域 {actual['域']}"
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
