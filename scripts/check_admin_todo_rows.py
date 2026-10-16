#!/usr/bin/env python
"""仪表盘待办卡 ↔ 后端 `admin_total` 求和项对账（P1 #10 防复发，2026-10-15 大审查）。

为什么需要它：仪表盘待办卡的 `total` 是**前端自己把 rows 加起来**的，而后端
`admin/todo_service.todo_counts` 的 `admin_total` 是另一份求和——两份清单各改各的，
就出现过大审查实测的假绿：只有「资金异常 / 报损赔偿 / 活动订单作废 / 待评估跟进 /
待结清跟进」时，卡片显示绿色「全部处理完毕」，**同屏侧边栏徽标 > 0**（同一屏两个数字互相
否认，且被掩盖的正是资金异常这类最该看见的事）。与门禁"失败禁假 0"同族。

检查项（[FAIL]）：
  - 后端 `admin_total` 的每个求和项，必须出现在前端 `Dashboard.tsx` 的
    `TODO_ROWS`（超管）或 `STAFF_TODO_ROWS`（member.manage）里 → 否则卡片会漏计。
允许前端**多列**行（`order_pending_manual` 就不在 `admin_total` 里）：多列只会让卡片数字
偏大（保守方向），不会假绿，故不报错。
后端新增求和项而忘记加行 → 本检查红；前端删行 → 本检查红。

**不做的**（如实声明）：不校验语义（行标签文案、跳转路由是否合理），也不校验权限视角
（超管是否持有 member.manage 由 `check_rbac_consistency.py` 管）。

退出码：缺行 → 1（gate 硬失败）；否则 0。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SERVICE = "backend/domain/admin/todo_service.py"
DASHBOARD = "admin-web/src/pages/Dashboard.tsx"


def _compound_rhs(text: str, start: int) -> str:
    """取 `+=` 右侧到语句结束（括号配平；回到零深度后的换行即止）。

    为什么要配平而不是按行取：真实写法里有跨多行的
    `counts["admin_total"] += (counts[a] + counts[b])`——按行取会把两个求和项都漏掉，
    于是检查器自己变成"看似通过"的摆设（这本身就是本轮防的那类假绿）。
    """
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                break
        elif ch == "\n" and depth == 0:
            break
        i += 1
    return text[start:i]


def admin_total_keys(root: pathlib.Path | None = None) -> set[str]:
    """后端 admin_total 的求和项：`sum(counts[k] for k in (...))` 元组 + 各处 `+=` 的键。"""
    root = root or ROOT
    text = (root / SERVICE).read_text(encoding="utf-8")
    keys: set[str] = set()
    m = re.search(
        r'counts\["admin_total"\]\s*=\s*sum\(\s*counts\[k\]\s*for\s*k\s*in\s*\((.*?)\)\s*,?\s*\)',
        text,
        re.S,
    )
    if not m:
        raise SystemExit(
            f"[FAIL] {SERVICE}: 找不到 admin_total 的 sum(...) 求和块（本检查需同步更新）"
        )
    keys.update(re.findall(r'"([a-z_]+)"', m.group(1)))
    for m2 in re.finditer(r'counts\["admin_total"\]\s*\+=', text):
        keys.update(re.findall(r'counts\["([a-z_]+)"\]', _compound_rhs(text, m2.end())))
    return keys


def frontend_row_keys(root: pathlib.Path | None = None) -> set[str]:
    """前端待办卡两张行表（超管 TODO_ROWS / member.manage STAFF_TODO_ROWS）的 key 集合。"""
    root = root or ROOT
    text = (root / DASHBOARD).read_text(encoding="utf-8")
    keys: set[str] = set()
    for name in ("TODO_ROWS", "STAFF_TODO_ROWS"):
        m = re.search(rf"const {name}:[\s\S]*?=\s*\[([\s\S]*?)\n\];", text)
        if not m:
            raise SystemExit(f"[FAIL] {DASHBOARD}: 找不到 {name} 行表（本检查需同步更新）")
        keys.update(re.findall(r'key:\s*"([a-z_]+)"', m.group(1)))
    return keys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    backend = admin_total_keys()
    frontend = frontend_row_keys()
    missing = sorted(backend - frontend)
    print(f"[i] admin_total 求和项 {len(backend)} 个；仪表盘行 {len(frontend)} 个")
    if missing:
        print(
            "[FAIL] 后端计入 admin_total 但仪表盘待办卡没有行（会显示绿色「全部处理完毕」"
            "而侧边栏徽标 > 0）："
        )
        for k in missing:
            print(f"    - {k}")
        return 1
    print("[OK] 仪表盘行覆盖 admin_total 全部求和项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
