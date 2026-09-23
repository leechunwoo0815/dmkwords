"""文案 emoji 门禁（R13d，2026-09-23；口径见 `docs/15 §21.3`）。

**管什么**：把 §21.2「文案 emoji 一律清出」的口径从**小程序**扩到**管理端 + 后端导出文案**——
用户可见的地方不许出现平台 emoji（三端渲染不一致、无令牌控制）：

  ① `admin-web/src/**/*.{ts,tsx}`：去注释（`/* */` 与 `//`）后扫，emoji 必须为 0
  ② `backend/**/*.py`：按 AST 取**字符串字面量**，emoji 必须为 0；**docstring 跳过**（文档不是用户文案）

排版字形（`TYPO_GLYPHS`：✓ ✕ ★ ☆ ▶ ● ◆ ※ 等）是设计系统文字符号，白名单放行（同 R13c 口径）。

**为什么独立于 `check_miniapp_style`**：那个只管小程序（R13c）；管理端与导出是同一口径的另一端，
塞一起会让"扫什么范围"含混——本项目的老毛病就是"口径写在两处必分叉"（错误库 §一百零二）。

三重自证（照该项目惯例）：
  S1 注入自检：内置违例/合规样本，检测器必须"该报的报、不该报的不报"
  S2 空扫自检：扫到 0 个文件 = FAIL（扫描范围损坏）
  S3 下限自检：文件数低于常识下限 = FAIL

用法：`.venv/bin/python -m scripts.check_copy_emoji`（退出码 0 = 全零）
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 平台 emoji 判定（与 check_miniapp_style.RE_EMOJI 同区间）
RE_EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f]")

#: 排版字形白名单（设计系统文字符号，不是平台 emoji）——与 R13c 的 TYPO_GLYPHS 同源
TYPO_GLYPHS = (
    "\u2713\u2714\u2715\u2716\u2717\u2718\u2605\u2606\u25b6\u25c0\u25b2\u25bc"
    "\u25cf\u25cb\u25a0\u25a1\u25c6\u25c7\u203b\u2039\u203a\u00b7"
)

#: 豁免白名单（路径后缀, 符号, 原因, 日期）——初始 0 条，遇一例议一例（同 R13c 惯例）
WHITELIST: list[tuple[str, str, str, str]] = []

#: S3 下限：两个扫描面的文件数不得低于此（扫描范围损坏会掉到 0）
MIN_TS = 40
MIN_PY = 40


def _strip_ts_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def _rel(p: Path) -> str:
    """相对仓库根的展示路径；不在仓库内（自证夹具在 /tmp）时原样返回。"""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def scan_admin_web(base: Path = ROOT / "admin-web" / "src"):
    """① 管理端 TS/TSX：去注释后按行找 emoji。"""
    out: list[tuple[str, int, str]] = []
    files = sorted(list(base.rglob("*.ts")) + list(base.rglob("*.tsx")))
    for f in files:
        if "node_modules" in f.parts:
            continue
        rel = _rel(f)
        for i, line in enumerate(_strip_ts_comments(f.read_text(encoding="utf-8")).split("\n"), 1):
            hits = [c for c in RE_EMOJI.findall(line) if c not in TYPO_GLYPHS]
            if hits:
                out.append((rel, i, "".join(hits)))
    return out, len(files)


def scan_backend(base: Path = ROOT / "backend"):
    """② 后端：AST 取字符串字面量（跳过 docstring）。"""
    out: list[tuple[str, int, str]] = []
    files = sorted(base.rglob("*.py"))
    for f in files:
        if "__pycache__" in f.parts:
            continue
        rel = _rel(f)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误另有门禁
            continue
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings:
                continue
            hits = [c for c in RE_EMOJI.findall(node.value) if c not in TYPO_GLYPHS]
            if hits:
                out.append((rel, getattr(node, "lineno", 0), "".join(hits)))
    return out, len(files)


def _whitelisted(rows: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    if not WHITELIST:
        return rows
    kept = []
    for rel, ln, sym in rows:
        if any(rel.endswith(suffix) and sym == s for suffix, s, _r, _d in WHITELIST):
            continue
        kept.append((rel, ln, sym))
    return kept


def self_test() -> list[str]:
    """S1 注入自检：违例必命中 / 合规不误报。"""
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="copy-emoji-selftest-"))
    try:
        (tmp / "admin-web" / "src").mkdir(parents=True)
        (tmp / "admin-web" / "src" / "bad.tsx").write_text(
            'const a = "✅ 签到成功";\n'  # 违规：文案里的 emoji
            "// 注释里的 ⚠ 不算违规\n"
            'const ok = "已打卡 ✓ ✕ ★";\n',  # 合规：排版字形
            encoding="utf-8",
        )
        (tmp / "backend").mkdir(parents=True)
        (tmp / "backend" / "bad.py").write_text(
            '"""模块 docstring：⚠ 这里不算违规。"""\n'
            "MSG = '⚠ 共 N 条'\n"  # 违规：字符串字面量
            'OK = "已归档 ✓"\n',  # 合规：排版字形
            encoding="utf-8",
        )
        bad_ts, n_ts = scan_admin_web(tmp / "admin-web" / "src")
        bad_py, n_py = scan_backend(tmp / "backend")
        if not any(r[0].endswith("bad.tsx") for r in bad_ts):
            failures.append(f"S1 管理端漏检: {bad_ts}")
        if any("✓" in r[2] or "✕" in r[2] for r in bad_ts):
            failures.append(f"S1 管理端误报排版字形: {bad_ts}")
        if n_ts < 1:
            failures.append("S1 管理端样本未扫到文件")
        if not any(r[0].endswith("bad.py") for r in bad_py):
            failures.append(f"S1 后端漏检: {bad_py}")
        if any(r[0].endswith("bad.py") and r[1] == 1 for r in bad_py):
            failures.append(f"S1 后端误报 docstring: {bad_py}")
        if n_py < 1:
            failures.append("S1 后端样本未扫到文件")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="文案 emoji 门禁 R13d（管理端 + 后端导出）")
    parser.add_argument("--dry", action="store_true", help="只打印，不判退出码")
    args = parser.parse_args()

    errors: list[str] = list(self_test())

    ts_rows, n_ts = scan_admin_web()
    py_rows, n_py = scan_backend()
    ts_rows = _whitelisted(ts_rows)
    py_rows = _whitelisted(py_rows)

    print("=" * 64)
    print("check_copy_emoji — 文案 emoji 门禁（R13d，docs/15 §21.3）")
    print(f"扫描面: admin-web/src {n_ts} 个文件 / backend {n_py} 个 .py")
    print(f"R13d① 管理端文案 emoji: {len(ts_rows)}")
    print(f"R13d② 后端字符串字面量 emoji（docstring 除外）: {len(py_rows)}")

    if n_ts == 0:
        errors.append(f"S2 管理端扫到 0 个文件——扫描范围损坏（期望 ≥{MIN_TS}）")
    if n_py == 0:
        errors.append(f"S2 后端扫到 0 个文件——扫描范围损坏（期望 ≥{MIN_PY}）")
    if 0 < n_ts < MIN_TS:
        errors.append(f"S3 管理端文件数 {n_ts} < {MIN_TS}——扫描范围异常")
    if 0 < n_py < MIN_PY:
        errors.append(f"S3 后端文件数 {n_py} < {MIN_PY}——扫描范围异常")

    for rel, ln, sym in ts_rows:
        errors.append(f"R13d① {rel}:{ln} 管理端文案里有 emoji {sym!r}（图标走 AntD 组件/资产）")
    for rel, ln, sym in py_rows:
        errors.append(
            f"R13d② {rel}:{ln} 后端字符串字面量里有 emoji {sym!r}（导出/提示文案要纯文本）"
        )

    if errors:
        print("\nFAIL:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        return 0 if args.dry else 1
    print("\nPASS: 管理端与后端导出文案零 emoji（排版字形白名单除外）✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
