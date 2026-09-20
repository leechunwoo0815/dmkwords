#!/usr/bin/env python
"""媒体纪律机械门禁（fix44 R4：专家 Q8 三条机化——"靠人记得"已证明必败，改机器看得住）。

三条规则：
  M1 落盘单出口: backend/ 内写媒体文件（`open(...,"wb"/"xb"/"ab")` 或 PIL `*.save(<路径>)`）
                 只允许**三个白名单出口**：
                   backend/common/file_storage.py        —— 上传/回压（normalize_image）
                   backend/domain/reading_circle/art.py  —— Canvas.finish（图标/头像/勋章 PNG）
                   backend/domain/reading_circle/card_render.py —— save_jpeg（卡片/海报/报告 JPEG）
                 别处出现 = 长出第三套画法（体积/规格/破缓存 token 三条纪律同时失守，
                 2026-09-17 统一收口前的原始故障形状）。
                 范围（红线 30 要求明说）：`scripts/` 下的一次性生成/运维脚本**不在 M1 范围**
                 ——它们不跑在生产请求路径上，且多数写的是小程序资产目录而非 uploads；
                 其中"会删东西"的脚本另受 M3 管。
  M2 破缓存单出口: `?v=` 只允许出现在 backend/common/file_utils.py。
                 媒体 URL 必须带版本 token（TD-8，docs/08），token 必须来自 `media_version()`
                 = 文件名随机片段；别处手写 `?v=`（写死 → 重生成后永远吃旧图；每次新值 → 缓存永不命中）
                 即绕过单源。扫描范围 backend/ + admin-web/src/ + miniapp/（前端也会拼 URL）。
  M3 清理脚本默认 dry-run: scripts/ 下名字含 clean/cleanup/purge/optimize/regen/trash 的脚本必须
                 (a) 有**正向** apply 开关（--apply / --trash DIR / --no-dry-run / --force / APPLY=）；
                 (b) 破坏性调用（os.remove/unlink/rmtree/shutil.move/open wb/Path.write_*）
                     位于正向 apply 守卫内——支持三种守卫形状：`if apply:` 分支、
                     `if not apply: continue/return/raise` 早退、经"只在守卫内被调用"的辅助函数间接执行；
                 (c) 不得把安全阀做成 opt-in 的 `--dry-run`（`action="store_true"` 且 default≠True）
                     —— 那种写法**默认就是破坏性的**（2026-09-20 实测 regen_covers.py 即此形）。

三重自证（防"检查器自身假绿"）：
  S1 注入自检: 对内置违例样本运行三条检测器，必须全部命中
  S2 空结果自检: 扫到 0 个 backend py / 0 个候选脚本 = FAIL（盲区形状）
  S3 基线数字: 打印扫描文件/脚本计数，低于常识下限 = FAIL
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BACKEND = REPO / "backend"
SCRIPTS = REPO / "scripts"

#: M1 落盘白名单（相对仓库根的路径）——新增出口必须改这里并说明理由（红线 31：改规则必改本清单）
WRITE_EXITS = {
    "backend/common/file_storage.py",
    "backend/domain/reading_circle/art.py",
    "backend/domain/reading_circle/card_render.py",
}

#: M2 破缓存白名单
CACHE_BUST_EXITS = {"backend/common/file_utils.py"}

#: M2 扫描根（相对仓库根）
CACHE_BUST_ROOTS = ("backend", "admin-web/src", "miniapp")

#: M3 候选脚本（名字命中即纳入"会改东西"的假设，宁可多查）
CLEANUP_NAME_RE = re.compile(r"(clean|purge|optimize|regen|trash)", re.I)

#: 正向 apply 守卫名（**不含 dry_run**——`dry_run` 单独出现属 opt-in 安全阀，见 M3c）
APPLY_GUARD_RE = re.compile(r"\b(apply|force|no_dry_run|trash|confirm|execute)\b", re.I)

#: 内存缓冲名（`.save(buf)` 不落盘，M1 不判）
BUFFER_NAMES = {"buf", "buffer", "bio", "out_buf", "tmp_buf", "stream", "fp", "f", "fh", "memory"}

#: 临时产物名（删自己的 tmp/cache 不算"清理用户数据"，M3b 不判）
TMP_NAME_RE = re.compile(r"tmp|temp|scratch|cache", re.I)


def _tmp_names(tree: ast.AST) -> set[str]:
    """局部变量里指向临时产物的名字（如 `out = ROOT / "tmp-icon-opt.png"`）。

    用途：区分"删自己的临时文件"（dry-run 下也安全）与"删用户数据/媒体"。
    """
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and TMP_NAME_RE.search(ast.unparse(n.value)):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


#: 明显的临时路径字面量（`/tmp/x`、`tmp-icon-opt.png` …）——删除它们不算破坏用户数据
TMP_LITERAL_RE = re.compile(r"(^|[/\"'])tmp[-_/]|/tmp/", re.I)


def _touches_temp(node: ast.Call, tmp_names: set[str]) -> bool:
    """该破坏性调用是否在动临时产物：接收者（`out.unlink()`）或首个实参是临时名/临时路径。"""
    exprs: list[str] = []
    if isinstance(node.func, ast.Attribute):
        exprs.append(ast.unparse(node.func.value))
    exprs.extend(ast.unparse(a) for a in node.args)
    for e in exprs:
        if e in tmp_names or TMP_LITERAL_RE.search(e.strip("'\"")):
            return True
    return False


DESTRUCTIVE_CALLS = {
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("shutil", "rmtree"),
    ("shutil", "move"),
    ("shutil", "copy"),
}


# ---------------- M1 落盘单出口 ----------------


def _is_path_like(node: ast.AST) -> bool:
    """判断 `.save(x)` 的 x 是否指向**磁盘路径**（内存缓冲不算）。

    实例（2026-09-20 实树注入自证）：`img.save('X.png')` / `img.save(path)` /
    `img.save(os.path.join(d, n))` 全部算路径；`img.save(buf)`（BytesIO）不算。
    """
    if isinstance(node, ast.Name):
        return node.id.lower() not in BUFFER_NAMES
    if isinstance(node, ast.Constant):
        # 字面量：字符串当路径（内存缓冲永远是变量，不会是字面量）
        return isinstance(node.value, (str, bytes))
    if isinstance(node, ast.Call):
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None) or ""
        if name in {"BytesIO", "StringIO"}:
            return False
        return True
    return isinstance(node, (ast.JoinedStr, ast.BinOp, ast.Attribute))


def scan_backend_writes(base: Path) -> list[tuple[str, int, str]]:
    """M1：backend 内非白名单的媒体落盘。"""
    out: list[tuple[str, int, str]] = []
    for py in sorted(base.rglob("*.py")):
        rel = str(py.relative_to(base.parent)) if base.name == "backend" else str(py)
        if rel in WRITE_EXITS:
            continue
        try:
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError) as e:  # pragma: no cover - 语法错误另有门禁
            out.append((rel, 0, f"解析失败: {e}"))
            continue
        lines = src.split("\n")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # open(..., "wb")
            if isinstance(func, ast.Name) and func.id == "open" and len(node.args) >= 2:
                mode = node.args[1]
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    m = mode.value
                    if ("w" in m or "a" in m or "x" in m) and "b" in m:
                        out.append((rel, node.lineno, lines[node.lineno - 1].strip()))
            # PIL img.save(<路径>)
            if isinstance(func, ast.Attribute) and func.attr == "save" and node.args:
                if _is_path_like(node.args[0]):
                    out.append((rel, node.lineno, lines[node.lineno - 1].strip()))
    return out


# ---------------- M2 破缓存单出口 ----------------


def scan_cache_bust(root: Path) -> list[tuple[str, int, str]]:
    """M2：`?v=` 出现在非白名单文件。"""
    out: list[tuple[str, int, str]] = []
    for sub in CACHE_BUST_ROOTS:
        base = root / sub
        if not base.exists():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix not in {".py", ".ts", ".tsx", ".js", ".wxml", ".json"}:
                continue
            if "node_modules" in f.parts or "dist" in f.parts:
                continue
            rel = str(f.relative_to(root))
            if rel in CACHE_BUST_EXITS:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # pragma: no cover
                continue
            for i, line in enumerate(text.split("\n"), 1):
                if "?v=" in line:
                    out.append((rel, i, line.strip()))
    return out


# ---------------- M3 清理脚本默认 dry-run ----------------


def _call_path(node: ast.Call) -> tuple[str, str] | None:
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        return (f.value.id, f.attr)
    if isinstance(f, ast.Name):
        return ("", f.id)
    return None


def _is_destructive(node: ast.Call) -> bool:
    cp = _call_path(node)
    if cp is None:
        return False
    if cp in DESTRUCTIVE_CALLS:
        return True
    mod, name = cp
    if name in {"unlink", "rmdir", "write_bytes", "write_text"}:
        return True
    if name == "open" and len(node.args) >= 2:
        mode = node.args[1]
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            m = mode.value
            return ("w" in m or "a" in m or "x" in m) and "b" in m
    if name == "move" and mod == "shutil":
        return True
    return False


def _guard_rows(tree: ast.AST) -> set[int]:
    """返回"受正向 apply 守卫保护"的语句行号集合。

    守卫三种形状：
      ① `if <apply>:` 分支内的语句；
      ② `if not <apply>: continue/return/raise` —— 早退之后的所有语句（orelse 与后续）；
      ③ 上述两种块内的**任意嵌套**（循环/with/try 里都算）。
    `dry_run` 单独出现**不算**守卫（它是 opt-in 安全阀，另由 M3c 判）。
    """
    guarded: set[int] = set()

    def mark(node: ast.AST) -> None:
        for n in ast.walk(node):
            ln = getattr(n, "lineno", None)
            if ln:
                guarded.add(ln)

    def scan_block(body: list[ast.stmt], active: bool) -> None:
        passed = active
        for stmt in body:
            if passed:
                mark(stmt)
                continue
            if isinstance(stmt, ast.If):
                src_test = ast.unparse(stmt.test)
                mentions = bool(APPLY_GUARD_RE.search(src_test))
                negated = bool(re.search(r"\bnot\b", src_test))
                exits = any(isinstance(s, (ast.Continue, ast.Return, ast.Raise)) for s in stmt.body)
                # ① 正向守卫
                scan_block(stmt.body, mentions and not negated)
                if mentions and negated and exits:
                    # ② 早退守卫：本 if 与后续语句都受保护
                    mark(stmt)
                    passed = True
                    scan_block(stmt.orelse, True)
                else:
                    scan_block(stmt.orelse, False)
                continue
            # 其它复合语句（for/while/with/try）：只递归子块，不整块标记
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(stmt, field, None)
                if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                    scan_block(sub, False)
            for handler in getattr(stmt, "handlers", []) or []:
                scan_block(handler.body, False)

    scan_block(getattr(tree, "body", []), False)
    return guarded


def _guarded_only_functions(tree: ast.AST, guarded: set[int]) -> set[str]:
    """**只在**守卫内被调用的函数名集合（如 `_write` / `_drop_old`）。

    守卫可经辅助函数间接生效：`if args.apply: _drop_old(...)` → `_drop_old` 体内的
    `os.remove` 同样受守卫。判定：该函数有调用点，且**不存在**任何守卫外调用点。
    说明：只追一层；A 受守卫调用 B、B 再调 C 时不追踪 C（保守方向：宁可报也不漏）。
    """
    func_names = {
        n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    in_guard: set[str] = set()
    out_guard: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in func_names:
            (in_guard if n.lineno in guarded else out_guard).add(n.func.id)
    return in_guard - out_guard


def scan_cleanup_script(path: Path) -> list[str]:
    """M3：(a) 正向 apply 开关 (b) 破坏性调用在守卫内 (c) 禁 opt-in --dry-run。"""
    issues: list[str] = []
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"语法错误（无法做 M3 分析）: {e}"]

    # (a) 必须有正向 apply 开关
    positive_flag = re.search(
        r'add_argument\(\s*["\']--(apply|no-dry-run|force|trash)["\']|"--apply"\s+in\s+sys\.argv|'
        r"--no-dry-run",
        src,
    )
    if not positive_flag:
        issues.append("(a) 缺正向 apply 开关（--apply / --trash DIR / --no-dry-run / --force）")

    # (c) 禁 opt-in --dry-run（action=store_true 且无 default=True → 默认破坏性）
    for m in re.finditer(r"add_argument\(\s*[\"']--dry-run[\"'](.*?)\)\s*\n", src, re.S):
        seg = m.group(1)
        if "store_true" in seg and "default=True" not in seg.replace(" ", ""):
            issues.append("(c) 安全阀是 opt-in 的 --dry-run（action=store_true，默认即破坏性）")
            break

    # (b) 破坏性调用必须在守卫内
    guarded = _guard_rows(tree)
    guarded_only = _guarded_only_functions(tree, guarded)
    tmp_names = _tmp_names(tree)
    lines = src.split("\n")
    enclosing: dict[int, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for x in ast.walk(n):
                if hasattr(x, "lineno"):
                    enclosing[x.lineno] = n.name
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and _is_destructive(n)):
            continue
        # 删自己的临时产物（tmp/cache 变量或字面量路径）不算破坏用户数据
        if _touches_temp(n, tmp_names):
            continue
        fn = enclosing.get(n.lineno, "")
        if n.lineno in guarded or fn in guarded_only:
            continue
        issues.append(
            f"(b) 第 {n.lineno} 行破坏性调用不在 apply 守卫内: {lines[n.lineno - 1].strip()}"
        )
    return issues


# ---------------- 主流程 ----------------


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    errors: list[str] = []
    writes = scan_backend_writes(BACKEND)
    busts = scan_cache_bust(REPO)
    scripts = sorted(p for p in SCRIPTS.glob("*.py") if CLEANUP_NAME_RE.search(p.name))

    n_py = len(list(BACKEND.rglob("*.py")))
    print(
        f"基线数字: backend .py {n_py} 个 / 清理候选脚本 {len(scripts)} 个 / 落盘白名单 {len(WRITE_EXITS)} 个"
    )
    print(f"M1 落盘单出口: 违规 {len(writes)}")
    print(f"M2 破缓存单出口: 违规 {len(busts)}")
    print(f"M3 清理脚本默认 dry-run: 候选 {len(scripts)} 个")

    if n_py < 20 or not scripts:
        errors.append(f"S2 空结果自检失败: backend py={n_py} / 候选脚本={len(scripts)}（盲区形状）")

    for f, ln, snippet in writes:
        errors.append(
            f"M1 {f}:{ln} backend 内裸落盘（走 file_storage / Canvas.finish / save_jpeg）: {snippet}"
        )
    for f, ln, snippet in busts:
        errors.append(
            f"M2 {f}:{ln} `?v=` 出现在非白名单文件（破缓存只走 file_utils.media_version）: {snippet}"
        )
    for p in scripts:
        for issue in scan_cleanup_script(p):
            errors.append(f"M3 scripts/{p.name} {issue}")

    print("=" * 64)
    if errors:
        for e in errors:
            print(f"✗ {e}")
        print(f"FAIL: 媒体纪律 {len(errors)} 项违规")
        return 1
    print("PASS: 媒体纪律三条规则全零（M1 落盘单出口 / M2 破缓存单出口 / M3 清理脚本默认 dry-run）")
    return 0


def self_test() -> int:
    """S1 注入自检：三类违例必须全部命中（含"合法形状不得误报"反向样本）。"""
    tmp = Path(tempfile.mkdtemp(prefix="media-discipline-"))
    bad = 0
    try:
        # M1：非白名单文件里的落盘 + 白名单文件里的落盘（后者不得误报）
        (tmp / "backend/domain").mkdir(parents=True)
        (tmp / "backend/common").mkdir(parents=True)
        (tmp / "backend/domain/rogue.py").write_text(
            "def f(img, path):\n    img.save(path, 'PNG')\n    open(path, 'wb').write(b'x')\n",
            encoding="utf-8",
        )
        (tmp / "backend/domain/okish.py").write_text(
            "from io import BytesIO\n\n\ndef f(img):\n    buf = BytesIO()\n    img.save(buf, 'PNG')\n",
            encoding="utf-8",
        )
        hits = scan_backend_writes(tmp / "backend")
        if len(hits) != 2:
            print(f"✗ S1-M1 注入未全命中（期望 2 命中，实得 {len(hits)}）: {hits}")
            bad += 1
        # M2：非白名单 / 白名单
        (tmp / "backend/common/file_utils.py").write_text('U = "?v=1"\n', encoding="utf-8")
        (tmp / "admin-web/src").mkdir(parents=True)
        (tmp / "admin-web/src/api.ts").write_text('const u = "/x?v=" + 1\n', encoding="utf-8")
        busts = scan_cache_bust(tmp)
        if len(busts) != 1 or "api.ts" not in busts[0][0]:
            print(f"✗ S1-M2 注入未全命中（期望仅 admin-web 1 命中）: {busts}")
            bad += 1
        # M3：三种非法形（opt-in dry-run / 无 apply 开关且裸删 / 守卫外破坏）+ 一种合法形不得误报
        (tmp / "scripts").mkdir(parents=True)
        (tmp / "scripts/regen_bad.py").write_text(
            "import argparse, os\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--dry-run', action='store_true')\n"
            "args = ap.parse_args()\n"
            "if not args.dry_run:\n    os.remove(DATA + '/x.jpg')\n",
            encoding="utf-8",
        )
        (tmp / "scripts/purge_bad.py").write_text(
            "import os\n\n\ndef main():\n    os.remove(DATA + '/y.jpg')\n",
            encoding="utf-8",
        )
        (tmp / "scripts/cleanup_good.py").write_text(
            "import argparse, os\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--apply', action='store_true')\n"
            "args = ap.parse_args()\n"
            "if not args.apply:\n    print('dry'); raise SystemExit(0)\n"
            "os.remove(DATA + '/z.jpg')\n",
            encoding="utf-8",
        )
        issues_bad1 = scan_cleanup_script(tmp / "scripts/regen_bad.py")
        issues_bad2 = scan_cleanup_script(tmp / "scripts/purge_bad.py")
        issues_ok = scan_cleanup_script(tmp / "scripts/cleanup_good.py")
        if not issues_bad1 or not issues_bad2:
            print(f"✗ S1-M3 注入未命中: regen_bad={issues_bad1} purge_bad={issues_bad2}")
            bad += 1
        if issues_ok:
            print(f"✗ S1-M3 反向样本误报: {issues_ok}")
            bad += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（M1/M2/M3 违例全命中 + 合法样本零误报）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
