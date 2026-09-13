#!/usr/bin/env python
"""RBAC 三方一致对账（宪法 §五.2「前端路由守卫 ↔ 后端 require_perm ↔ 菜单按钮显隐」）。

为什么需要它：宪法明写"对账脚本入门禁"，但 2026-09-12 全维度审查发现 **gate 9 步里没有这一步**，
于是权限码出现了三处不一致（实证）：
  - 后端用了 `config.update` / `audit.view`，而它们**不在任何权限目录里**
    → "没人持有的权限码"：staff 恒 403（行为对，语义不可判定）；
  - 目录声明了 `activity.manage`，后端**零处挂该守卫**（端点实挂 member.manage）→ 死权限；
  - 目录 3 个码（activity/audio/quiz.manage）前端零拦截。
E-20260912-09 修复：把"仅超管"的码显式登记进 `SUPER_ADMIN_ONLY_PERMISSIONS`（单一事实源），
并以本脚本机械执法——**声明完整、引用可控、缺口可见**。

检查项：
  [FAIL] 后端 require_perm 引用的码必须已声明（STAFF ∪ SUPER_ADMIN_ONLY）
  [FAIL] 前端 hasPermission/perm 引用的码必须已声明（否则按钮/菜单**永远隐藏**）
  [WARN] 已声明但后端从未引用的码（死权限）
  [WARN] 已声明但前端零拦截的码（角色缺该码时 UI 会露出必然 403 的入口）

范围边界（如实声明，不假装全知）：本脚本做的是**码集合层面对账**；
"每个端点 ↔ 每个菜单项"的逐项映射需要人工评审（脚本不做语义推断）。
未接线的码用 UNWIRED_ALLOWLIST 显式登记（含理由与归属），**不允许静默存在**。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 已声明但暂未接线：必须写明理由与归属（有据可查，禁止静默）
UNWIRED_ALLOWLIST: dict[str, str] = {
    "activity.manage": "F-L9 裁定保留（staff 目录断言依赖）；端点实挂 member.manage，接线评估挂后续批",
}


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def backend_used() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in (ROOT / "backend").rglob("*.py"):
        for m in re.finditer(r"require_perm\(\s*([^)]*)\)", _read(f)):
            for code in re.findall(r"""["']([A-Za-z_][A-Za-z_.]*)["']""", m.group(1)):
                out.setdefault(code, set()).add(str(f.relative_to(ROOT)))
    return out


def frontend_used() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    files = list((ROOT / "admin-web/src").rglob("*.tsx")) + list(
        (ROOT / "admin-web/src").rglob("*.ts")
    )
    for f in files:
        if f.name == "schema.d.ts":
            continue
        txt = _read(f)
        for pat in (
            r"""hasPermission\([^,]+,\s*["']([A-Za-z_][A-Za-z_.]*)["']""",
            r"""perm(?:ission)?s?\s*[:=]\s*["']([A-Za-z_][A-Za-z_.]*)["']""",
        ):
            for m in re.finditer(pat, txt):
                out.setdefault(m.group(1), set()).add(str(f.relative_to(ROOT)))
    return out


def main() -> int:
    from backend.domain.admin.service import (
        STAFF_PERMISSIONS,
        SUPER_ADMIN_ONLY_PERMISSIONS,
        all_permission_codes,
    )

    declared = set(all_permission_codes())
    be, fe = backend_used(), frontend_used()
    print(
        f"[rbac] 已声明 {len(declared)} 个码（staff {len(STAFF_PERMISSIONS)} + 仅超管 "
        f"{len(SUPER_ADMIN_ONLY_PERMISSIONS)}）| 后端引用 {len(be)} | 前端引用 {len(fe)}"
    )

    failed = 0
    undeclared_be = {c: v for c, v in be.items() if c not in declared}
    if undeclared_be:
        failed = 1
        print("✗ 后端引用了未声明的权限码（staff 恒 403、语义不可判定）：")
        for c, fs in sorted(undeclared_be.items()):
            print(f"   {c} ← {', '.join(sorted(x.split('/')[-1] for x in fs))}")

    undeclared_fe = {c: v for c, v in fe.items() if c not in declared}
    if undeclared_fe:
        failed = 1
        print("✗ 前端引用了未声明的权限码（按钮/菜单将永远隐藏）：")
        for c, fs in sorted(undeclared_fe.items()):
            print(f"   {c} ← {', '.join(sorted(fs))}")

    dead = sorted(declared - set(be) - set(UNWIRED_ALLOWLIST))
    if dead:
        print(f"⚠ 已声明但后端从未引用（死权限）{len(dead)}：{dead}")
    for code, why in sorted(UNWIRED_ALLOWLIST.items()):
        mark = "已声明未接线（已登记）" if code in declared else "登记了但不在目录里"
        print(f"⚠ {code}：{mark} —— {why}")

    ungated = sorted(declared - set(fe))
    if ungated:
        print(f"⚠ 前端零拦截的码（缺该码的角色会看到必然 403 的入口）{len(ungated)}：{ungated}")

    if failed:
        print("[rbac] ✗ 对账失败（修目录/引用，或把「暂未接线」写进 UNWIRED_ALLOWLIST 并注明理由）")
        return 1
    print("[rbac] ✓ 权限码三方一致（声明/后端引用/前端引用无未声明项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
