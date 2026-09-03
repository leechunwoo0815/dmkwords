# scripts/verify_architecture.py — 架构关（宪法第三节/第四节机械执法）
"""每条架构规则对应一个检查；退出码非 0 = 架构违规。
骨架期检查项：四件套齐全 / Router 违规 / 单文件行数 / SQLite 禁令 / import 白名单。
域就绪后逐步收紧：事件发布订阅对账、函数内 import 计数阈值。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DOMAINS = [
    "identity",
    "catalog",
    "circulation",
    "billing",
    "reading",
    "growth",
    "activity",
    "admin",
]
FOUR_PIECES = ["models.py", "schemas.py", "repository.py", "service.py", "router.py"]

ROUTER_VIOLATIONS = [
    (re.compile(r"db\.(query|add|commit|delete)\b"), "Router 层出现 ORM 操作"),
    (re.compile(r"\btry\s*:"), "Router 层出现 try/except"),
    (re.compile(r"raise\s+HTTPException"), "Router 层抛 HTTPException（应在 Service）"),
]

# A-1/T6：架构门禁扩 miniapp_router（原只扫 router.py，5 个 miniapp_router 执法真空）
ROUTER_PIECES = ("router.py", "miniapp_router.py")

MAX_FILE_LINES = 800


def check_four_pieces(errors: list[str]) -> None:
    for domain in DOMAINS:
        dpath = BACKEND / "domain" / domain
        if not dpath.is_dir():
            errors.append(f"域缺失: {domain}")
            continue
        for piece in FOUR_PIECES:
            if not (dpath / piece).is_file():
                errors.append(f"{domain}/{piece} 缺失（四件套不齐）")


def check_router_violations(errors: list[str]) -> None:
    for domain in DOMAINS:
        for piece in ROUTER_PIECES:
            router = BACKEND / "domain" / domain / piece
            if not router.is_file():
                continue
            text = router.read_text(encoding="utf-8")
            for pattern, msg in ROUTER_VIOLATIONS:
                if pattern.search(text):
                    errors.append(f"{domain}/{piece}: {msg}")


def check_lock_populate(errors: list[str]) -> None:
    """B-16/T2 防复发：所有 with_for_update 必须链 populate_existing（identity map 陷阱）。
    支持同行或下一行补链（reading 既有跨行先例）。"""
    for py in BACKEND.rglob("service.py"):
        if "__pycache__" in py.parts:
            continue
        lines = py.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if ".with_for_update()" not in line:
                continue
            if ".populate_existing()" in line:
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if ".populate_existing()" not in nxt:
                rel = py.relative_to(ROOT)
                errors.append(f"{rel}:{i + 1}: with_for_update 未链 populate_existing（B-16）")


def check_file_length(errors: list[str]) -> None:
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        lines = len(py.read_text(encoding="utf-8").splitlines())
        if lines > MAX_FILE_LINES:
            errors.append(f"{py.relative_to(ROOT)}: {lines} 行 > {MAX_FILE_LINES}（god file 禁止）")


def check_sqlite_ban(errors: list[str]) -> None:
    for py in list(BACKEND.rglob("*.py")) + list(ROOT.joinpath("scripts").rglob("*.py")):
        if "__pycache__" in py.parts or py.name == "verify_architecture.py":
            continue  # 跳过自身（检查器含禁词字面量）
        if py.name == "import_ecdict.py":
            continue  # C5 一次性导入工具：ECDICT 源文件 data/ecdict.db 本就是 SQLite，运行时库仍 MySQL-only
        text = py.read_text(encoding="utf-8").lower()
        if "sqlite" in text:
            errors.append(f"{py.relative_to(ROOT)}: 检测到 sqlite（宪法禁令）")


def check_import_whitelist(errors: list[str]) -> None:
    """common 不依赖域；域不依赖 admin。"""
    for py in (BACKEND / "common").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        if re.search(r"from\s+backend\.domain", text):
            errors.append(f"{py.relative_to(ROOT)}: common 依赖了业务域")
    for domain in DOMAINS:
        if domain == "admin":
            continue
        for py in (BACKEND / "domain" / domain).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8")
            if re.search(r"from\s+backend\.domain\.admin", text):
                errors.append(f"{py.relative_to(ROOT)}: 业务域反向依赖 admin")


def main() -> int:
    errors: list[str] = []
    check_four_pieces(errors)
    check_router_violations(errors)
    check_lock_populate(errors)
    check_file_length(errors)
    check_sqlite_ban(errors)
    check_import_whitelist(errors)

    if errors:
        print(f"架构关 FAIL（{len(errors)} 处违规）：")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print(
        "架构关 PASS：四件套齐全 / Router 零违规（含 miniapp_router）/ 锁定读均链 populate_existing"
        " / 行数达标 / 无 sqlite / import 白名单通过"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
