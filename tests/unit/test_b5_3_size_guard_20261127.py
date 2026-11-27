# tests/unit/test_b5_3_size_guard_20261127.py — 行数两级闸（后端 700/800 + 前端 700/800）
"""审查① A7 / ② P2：god-file 只有一条"800 行硬红线"，于是每次都是**撞线后急救**
（本仓历史：`wm10_service` 826 → 抽退会域、`registry` 814 → 抽锁名/失败兜底）。
B5-3 把它做成两级：**700 起预警（不阻断）+ 800 判红**，并补上前端这一半——
`ServerMonitor.tsx` 963 行是管理端最大的单文件页面。

本文件钉住四件事：
  1. 后端架构关有 `WARN_FILE_LINES = 700` 且**预警不判红**（只打印）；
  2. 前端检查器已接进 gate，且 `--self-test` 通过（写坏检查器必须报得出来）；
  3. `ServerMonitor.tsx` 已拆到 ≤400 行，三个子组件都在 `pages/monitor/` 下且各 ≤400 行；
  4. 前端检查器排除自动生成的 `api/schema.d.ts`（14010 行，判它没有意义）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GATE = ROOT / "scripts/gate.sh"
ARCH = ROOT / "scripts/verify_architecture.py"
FRONTEND_CHECKER = ROOT / "scripts/check_frontend_size.py"


def test_backend_has_two_level_size_gate():
    """700 预警 + 800 判红；预警只打印不置 FAILED。"""
    src = ARCH.read_text(encoding="utf-8")
    assert "WARN_FILE_LINES = 700" in src, "后端缺 700 行预警线"
    assert "MAX_FILE_LINES = 800" in src, "800 行硬红线被改动"
    assert "check_file_length(errors, warnings)" in src, "预警没接进主流程"
    # 预警必须走 warnings 而不是 errors（写进 errors 就等于把 700 变成硬红线）
    body = src[src.index("def check_file_length") : src.index("def check_sqlite_ban")]
    assert "warnings.append" in body and "errors.append" in body
    # 预警行不能出现在 errors 分支
    assert "elif lines > WARN_FILE_LINES" in body


def test_frontend_checker_wired_and_self_test_passes():
    assert "check_frontend_size" in GATE.read_text(encoding="utf-8"), "前端行数检查未接进 gate"
    from scripts import check_frontend_size as cfs

    assert cfs.WARN_LINES == 700 and cfs.MAX_LINES == 800
    assert cfs.self_test() == 0, "前端行数检查器自证失败"


def test_frontend_checker_excludes_generated_contract_types():
    """`schema.d.ts` 是 openapi-typescript 生成的，行数由契约决定——判它没有意义。"""
    src = FRONTEND_CHECKER.read_text(encoding="utf-8")
    assert "schema.d.ts" in src
    # 白名单必须带理由且过期判红（同 verify_architecture 的白名单纪律）
    assert "ALLOWLIST" in src and "理由" in src


def test_server_monitor_is_split_into_small_components():
    """963 行的单页拆成主页面 + 3 个子组件；默认导出与路由路径不变（App.tsx 不用改）。"""
    page = ROOT / "admin-web/src/pages/ServerMonitor.tsx"
    lines = len(page.read_text(encoding="utf-8").splitlines())
    assert lines <= 400, f"ServerMonitor.tsx 仍有 {lines} 行（应 ≤400）"

    parts = sorted((ROOT / "admin-web/src/pages/monitor").glob("*.tsx"))
    assert len(parts) >= 3, f"pages/monitor/ 子组件不足 3 个：{parts}"
    for p in parts:
        n = len(p.read_text(encoding="utf-8").splitlines())
        assert n <= 400, f"{p.name} 仍有 {n} 行（子组件应 ≤400）"

    app = (ROOT / "admin-web/src/App.tsx").read_text(encoding="utf-8")
    assert "./pages/ServerMonitor" in app, "路由 import 路径被改了（应在原路径保留默认导出）"
    assert re.search(r"export default function ServerMonitor", page.read_text(encoding="utf-8")), (
        "默认导出名必须是 ServerMonitor（App.tsx 按名字/默认导入）"
    )
