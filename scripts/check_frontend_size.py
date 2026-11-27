#!/usr/bin/env python
# scripts/check_frontend_size.py — 前端 god-file 行数检查（B5-3，2026-11-27）
"""**前端行数红线**：与 `verify_architecture.check_file_length` 的后端两级口径对齐，
补上管理端前端的空白——后端早就 >800 判红、700~800 预警，前端一直没人看，
于是 `ServerMonitor.tsx` 长到 963 行、`MemberManage.tsx` 1325 行都没人拦。

两条规则：
  **R1 god file**：`> 800` 行判红；`700~800` 行只预警（不判红，提醒"下次改动前先抽模块"）。
  **R2 白名单过期**：进了 `ALLOWLIST` 的条目必须仍是超限文件——文件已被拆到 800 以下、
  或文件已不存在，都要判红并删除条目（防白名单长成第二个垃圾场，写法同
  `verify_architecture.py` 的 `EVENT_CONTRACT_WHITELIST` 过期判红）。

扫描面：`admin-web/src/**/*.ts|*.tsx`，**排除 `api/schema.d.ts`**——openapi-typescript 的
生成产物（14010 行），改它等于改生成器，行数判据在这里只会制造噪声。

自证（`--self-test`，夹具写进临时目录，不动仓库）：
  · S1 三类情况：>800 判红 / 700~800 只预警 / 白名单命中不判红；
  · S2 白名单过期判红（文件已掉到线下 / 指向不存在的文件又无命中）；
  · S3 生成产物不计入；空目录与不存在的目录不得假绿（fail-closed）。

退出码：0 = 通过；1 = 有超限或白名单过期；2 = 扫描失败（目录不存在/扫到 0 个文件，
路径写错必须先炸出来，不能假绿）。

用法：`python scripts/check_frontend_size.py [--self-test]`
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "admin-web" / "src"

#: god file 两级（与后端 `verify_architecture.MAX_FILE_LINES/WARN_FILE_LINES` 同值）
MAX_LINES = 800
WARN_LINES = 700

#: 自动生成的契约类型：超限只能改生成器（`pnpm gen:api`），行数判据无意义
GENERATED: frozenset[str] = frozenset({"api/schema.d.ts"})

#: 白名单：相对 `admin-web/src` 的路径 → **不拆的理由**（空理由不放行；条目过期判红）。
#: B5-3 只拆 ServerMonitor；剩下两个多列表巨页同批登记为下一轮，避免"边拆边长"。
ALLOWLIST: dict[str, str] = {
    "pages/MemberManage.tsx": "1325 行；B5-3 本轮未拆（3 个独立列表 + 2 个下拉），登记下一轮",
    "pages/Notifications.tsx": "891 行；B5-3 本轮未拆（同为多列表页），登记下一轮",
}


def _line_count(path: pathlib.Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def collect_sizes(root: pathlib.Path) -> dict[str, int]:
    """相对路径 → 行数；跳过生成产物。root 不可读时抛 OSError（fail-closed）。"""
    sizes: dict[str, int] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in GENERATED:
            continue
        sizes[rel] = _line_count(path)
    return sizes


def audit(sizes: dict[str, int], allowlist: dict[str, str]) -> tuple[list[str], list[str]]:
    """纯函数：返回 (判红项, 预警项)。自证与主流程共用，避免两套口径漂移。"""
    errors: list[str] = []
    warnings: list[str] = []
    for rel, lines in sorted(sizes.items()):
        if lines > MAX_LINES:
            if rel in allowlist:
                continue
            errors.append(f"R1 {rel}: {lines} 行 > {MAX_LINES}（god file 禁止，先抽组件）")
        elif lines > WARN_LINES:
            warnings.append(
                f"{rel}: {lines} 行（距 {MAX_LINES} 红线仅 {MAX_LINES - lines} 行）"
                "——下次改动前先抽模块，别等撞线再急救"
            )
    # 白名单过期：条目必须仍是超限文件，否则就是"债已还、牌子还挂着"
    for rel, why in sorted(allowlist.items()):
        if not why.strip():
            errors.append(f"R2 白名单条目 `{rel}` 缺理由（空理由不放行）")
        lines = sizes.get(rel)
        if lines is None:
            errors.append(f"R2 白名单条目 `{rel}` 指向的文件不存在或已排除——请删除该条目")
        elif lines <= MAX_LINES:
            errors.append(
                f"R2 白名单条目 `{rel}` 已过期（当前 {lines} 行 ≤ {MAX_LINES}）——请删除该条目"
            )
    return errors, warnings


def _report(errors: list[str], warnings: list[str], count: int) -> int:
    for w in warnings:
        print(f"[B5-3 行数预警] {w}")
    if errors:
        print(f"FOUND {len(errors)} 处前端行数问题（扫描 {count} 个文件）：")
        for e in errors:
            print("  ✗", e)
        print(
            f"\nFAIL: >{MAX_LINES} 行的页面必须拆组件；确属本轮不拆的写进 ALLOWLIST 并注明理由，"
            "拆完记得删条目（见 CLAUDE.md §六 模块交付制）"
        )
        return 1
    print(
        f"PASS: 前端行数检查通过（扫描 {count} 个文件，无 >{MAX_LINES} 行；"
        f"白名单 {len(ALLOWLIST)} 条均仍超限）✓"
    )
    return 0


def scan(root: pathlib.Path) -> tuple[dict[str, int] | None, str]:
    """返回 (相对路径→行数, 失败原因)；字典为 None 表示**扫描失败**（主流程退出码 2）。

    目录不存在 / 扫到 0 个文件都算失败：路径写错时若静默通过，红线就成了摆设。
    """
    if not root.is_dir():
        return None, f"找不到 {root}"
    try:
        sizes = collect_sizes(root)
    except OSError as exc:
        return None, str(exc)
    if not sizes:
        return None, f"{root} 下没有 .ts/.tsx（路径写错会假绿）"
    return sizes, ""


def _fill(path: pathlib.Path, lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// fixture\n" * lines, encoding="utf-8")


def self_test() -> int:
    bad = 0
    marks: list[str] = []

    def check(ok: bool, message: str) -> None:
        nonlocal bad
        if ok:
            marks.append(f"✓ {message}")
        else:
            marks.append(f"✗ {message}")
            bad += 1

    with tempfile.TemporaryDirectory(prefix="frontend-size-selftest-") as d:
        root = pathlib.Path(d)
        _fill(root / "pages" / "Big.tsx", MAX_LINES + 1)  # 801 → 判红
        _fill(root / "pages" / "Warn.tsx", WARN_LINES + 50)  # 750 → 只预警
        _fill(root / "pages" / "Ok.tsx", 120)  # 干净
        _fill(root / "api" / "schema.d.ts", MAX_LINES * 3)  # 生成产物 → 不计
        _fill(root / "pages" / "Debt.tsx", MAX_LINES + 100)  # 900 + 白名单 → 放行
        _fill(root / "pages" / "Paid.tsx", 200)  # 债已还，条目该删
        _fill(root / "pages" / "Untyped.ts", WARN_LINES + 1)  # .ts 也在扫描面

        sizes = collect_sizes(root)
        # S3：生成产物不进扫描面
        check("api/schema.d.ts" not in sizes, "S3 生成产物 api/schema.d.ts 未计入")
        check(
            {
                "pages/Big.tsx",
                "pages/Warn.tsx",
                "pages/Ok.tsx",
                "pages/Debt.tsx",
                "pages/Paid.tsx",
                "pages/Untyped.ts",
            }
            == set(sizes),
            "S3 扫描面只收 .ts/.tsx 且排掉生成产物",
        )

        allow = {
            "pages/Debt.tsx": "自证样本：本轮不拆",
            "pages/Paid.tsx": "自证样本：条目已过期",
            "pages/Ghost.tsx": "自证样本：文件不存在",
        }
        errors, warnings = audit(sizes, allow)
        joined = "\n".join(errors)
        warn_joined = "\n".join(warnings)

        # S1：三类情况各归各位
        check("pages/Big.tsx" in joined, "S1 >800 行判红")
        check("pages/Big.tsx" not in warn_joined, "S1 判红的文件不再重复预警")
        check("pages/Warn.tsx" in warn_joined, "S1 700~800 行只预警")
        check("pages/Warn.tsx" not in joined, "S1 700~800 行不判红")
        check("pages/Untyped.ts" in warn_joined, "S1 .ts 与 .tsx 同档扫描")
        check("pages/Ok.tsx" not in joined + warn_joined, "S1 干净文件零误报")
        check("pages/Debt.tsx" not in joined, "S1 白名单命中不判红")
        check("pages/Debt.tsx" not in warn_joined, "S1 白名单命中的文件不重复预警")

        # S2：白名单过期（掉到线下 / 文件没了）必须判红
        check(
            "pages/Paid.tsx" in joined and "已过期" in joined, "S2 白名单条目过期（已掉到线下）判红"
        )
        check(
            "pages/Ghost.tsx" in joined and "不存在" in joined, "S2 白名单条目指向不存在的文件判红"
        )

        # 空理由不放行
        extra, _ = audit(sizes, {"pages/Debt.tsx": "  "})
        check(any("缺理由" in e for e in extra), "S2 白名单空理由判红")

        # fail-closed：空目录 / 不存在的目录都必须走「扫描失败 → 退出码 2」
        empty = root / "empty"
        empty.mkdir()
        check(scan(empty)[0] is None, "S3 空目录判扫描失败（退出码 2，不假绿）")
        check(scan(root / "no-such-dir")[0] is None, "S3 不存在的目录判扫描失败（退出码 2）")
        check(scan(root)[0] is not None, "S3 正常目录不被误判失败")

        # 退出码映射：有判红项 → 1；全清 → 0（自证里吞掉 _report 的输出）
        with contextlib.redirect_stdout(io.StringIO()):
            red_code = _report(["R1 样本"], [], len(sizes))
            clean_code = _report([], ["仅预警"], len(sizes))
        check(red_code == 1, "S3 有判红项时退出码 1")
        check(clean_code == 0, "S3 只有预警/全清时退出码 0")

    for line in marks:
        if line:
            print(line)
    if bad:
        print(f"✗ S1/S2/S3 注入自检失败 {bad} 项")
        return 1
    print(
        "✓ S1/S2/S3 自检通过（>800 判红 / 700~800 只预警 / 白名单命中放行且不重复预警；"
        "白名单过期与空理由判红；生成产物与空目录/坏路径按预期处理）"
    )
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    sizes, reason = scan(SRC)
    if sizes is None:
        print(f"✗ 扫描失败：{reason}")
        return 2
    errors, warnings = audit(sizes, ALLOWLIST)
    return _report(errors, warnings, len(sizes))


if __name__ == "__main__":
    raise SystemExit(main())
