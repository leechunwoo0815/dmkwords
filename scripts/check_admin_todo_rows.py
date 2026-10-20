#!/usr/bin/env python
"""仪表盘待办卡 ↔ 后端 `admin_total` 求和项对账（P1 #10 防复发，2026-10-15 大审查）。

为什么需要它：仪表盘待办卡的 `total` 是**前端自己把 rows 加起来**的，而后端
`admin/todo_service.todo_counts` 的 `admin_total` 是另一份求和——两份清单各改各的，
就出现过大审查实测的假绿：只有「资金异常 / 报损赔偿 / 活动订单作废 / 待评估跟进 /
待结清跟进」时，卡片显示绿色「全部处理完毕」，**同屏侧边栏徽标 > 0**（同一屏两个数字互相
否认，且被掩盖的正是资金异常这类最该看见的事）。与门禁"失败禁假 0"同族。

检查项（[FAIL]）——**按视角分别断言**（2026-10-17 第六十九批·大审查 P2-19 修）
  - 超管视角（`admin.role == ROLE_SUPER_ADMIN` 块）：该块计入 `admin_total` 的每个求和项，
    必须出现在 `Dashboard.tsx` 的 `TODO_ROWS` 里；
  - 专员视角（`role_has_permission(admin.role, "member.manage")` 块）：该块计入 `admin_total`
    的每个求和项，必须出现在 `STAFF_TODO_ROWS` 里。
**为什么不能再用两表并集判超集**（这是 P2-19 的原始缺陷）：把某个键从 `STAFF_TODO_ROWS`
**挪进** `TODO_ROWS`，并集不变 → 脚本照样 `[OK]`，可**专员卡正是漏行的那一张**（超管没事），
于是 2026-10-15 P1 #10 那一幕（卡片绿色「全部处理完毕」vs 徽标 > 0）原样复发——
防假绿的检查器自己成了假绿。视角必须与后端角色分支**一一对应**。
允许前端**多列**行（如 `order_pending_manual` 就不在 `admin_total` 里）：多列只会让卡片数字
偏大（保守方向），不会假绿，故不报错。
后端新增求和项而忘记加行 → 本检查红；前端删行或**挪表** → 本检查红。

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

#: 后端两个角色分支的锚点行（视角分离的依据）。改 `todo_service.todo_counts` 的
#: 权限门写法就必须同步这里——找不到锚点直接 [FAIL]，不允许"静默降级成只查并集"。
SUPER_GATE = "if admin.role == AdminUser.ROLE_SUPER_ADMIN:"
STAFF_GATE = 'if role_has_permission(admin.role, "member.manage"):'


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


def _role_blocks(text: str) -> tuple[str, str]:
    """切出两个角色分支的源码块（超管 / member.manage 专员），供按视角取求和项。"""
    try:
        super_pos = text.index(SUPER_GATE)
        staff_pos = text.index(STAFF_GATE)
        return_pos = text.index("return counts", staff_pos)
    except ValueError as exc:  # 锚点行被改写 → 检查器失效，必须显式红
        raise SystemExit(
            f"[FAIL] {SERVICE}: 找不到角色分支锚点（{exc}）——本检查需同步更新"
            "（不允许退回并集判法：那正是 P2-19 的假绿来源）"
        ) from exc
    if not super_pos < staff_pos < return_pos:
        raise SystemExit(f"[FAIL] {SERVICE}: 角色分支锚点顺序异常（本检查需同步更新）")
    return text[super_pos:staff_pos], text[staff_pos:return_pos]


def _summed_keys(block: str, *, require_sum_block: bool) -> set[str]:
    """块内计入 `admin_total` 的键：`sum(counts[k] for k in (...))` 元组 + 各 `+=` 右侧。"""
    keys: set[str] = set()
    m = re.search(
        r'counts\["admin_total"\]\s*=\s*sum\(\s*counts\[k\]\s*for\s*k\s*in\s*\((.*?)\)\s*,?\s*\)',
        block,
        re.S,
    )
    if m:
        keys.update(re.findall(r'"([a-z_]+)"', m.group(1)))
    elif require_sum_block:
        raise SystemExit(
            f"[FAIL] {SERVICE}: 超管块里找不到 admin_total 的 sum(...) 求和块（本检查需同步更新）"
        )
    for m2 in re.finditer(r'counts\["admin_total"\]\s*\+=', block):
        keys.update(re.findall(r'counts\["([a-z_]+)"\]', _compound_rhs(block, m2.end())))
    return keys


def admin_total_keys_by_viewpoint(root: pathlib.Path | None = None) -> tuple[set[str], set[str]]:
    """后端 `admin_total` 求和项 **按视角**：返回 (超管块键集, member.manage 块键集)。"""
    root = root or ROOT
    text = (root / SERVICE).read_text(encoding="utf-8")
    super_block, staff_block = _role_blocks(text)
    return _summed_keys(super_block, require_sum_block=True), _summed_keys(
        staff_block, require_sum_block=False
    )


def frontend_row_keys(root: pathlib.Path | None = None) -> dict[str, set[str]]:
    """前端待办卡两张行表各自的 key 集合：{"TODO_ROWS": …, "STAFF_TODO_ROWS": …}。"""
    root = root or ROOT
    text = (root / DASHBOARD).read_text(encoding="utf-8")
    rows: dict[str, set[str]] = {}
    for name in ("TODO_ROWS", "STAFF_TODO_ROWS"):
        m = re.search(rf"const {name}:[\s\S]*?=\s*\[([\s\S]*?)\n\];", text)
        if not m:
            raise SystemExit(f"[FAIL] {DASHBOARD}: 找不到 {name} 行表（本检查需同步更新）")
        rows[name] = set(re.findall(r'key:\s*"([a-z_]+)"', m.group(1)))
    return rows


def find_missing(root: pathlib.Path | None = None) -> tuple[list[str], list[str]]:
    """按视角求缺行：返回 (超管视角缺行 `TODO_ROWS`, 专员视角缺行 `STAFF_TODO_ROWS`)。

    **判定只在这一处**：`main()` 与回归用例（`tests/unit/test_audit4_p2_batch1_20261017.py`）
    共用它——用例若自己重算判据，就等于同一口径两份实现（红线 154），
    改坏 `main()` 时用例照样绿（2026-10-17 首次变异自证即被这一点咬到）。
    """
    super_keys, staff_keys = admin_total_keys_by_viewpoint(root)
    rows = frontend_row_keys(root)
    return (
        sorted(super_keys - rows["TODO_ROWS"]),
        sorted(staff_keys - rows["STAFF_TODO_ROWS"]),
    )


#: 自证夹具（**结构必须与真实文件同形**：两个角色锚点 + sum 求和块 + return counts）
_FIXTURE_SERVICE = """
def todo_counts(self, admin):
    counts = {}
    if admin.role == AdminUser.ROLE_SUPER_ADMIN:
        counts["admin_total"] = sum(
            counts[k]
            for k in (
                "refund_pending",
                "withdrawal_pending",
            )
        )
        counts["admin_total"] += counts["member_follow_up"]
    if role_has_permission(admin.role, "member.manage"):
        counts["order_pending_manual"] = 1
        counts["admin_total"] += counts["order_pending_manual"]
    return counts
"""

_FIXTURE_DASHBOARD = """
const TODO_ROWS: Row[] = [
  { key: "refund_pending", label: "待审退款" },
  { key: "withdrawal_pending", label: "待审退会" },
  { key: "member_follow_up", label: "会员跟进" },
];
const STAFF_TODO_ROWS: Row[] = [
  { key: "order_pending_manual", label: "待确认收款" },
];
"""


def self_test() -> int:
    """S1 注入自检：**缺行必须被检出**，两侧齐全时零误报。

    为什么必须有（第八轮审查 P1-10 + 第七轮 P2-19 的教训）：本检查器第一版用"并集判超集"，
    可以被构造通过——自证样本正是防这种"判据本身假绿"。
    """
    import tempfile

    bad = 0
    with tempfile.TemporaryDirectory(prefix="todo-rows-selftest-") as d:
        root = pathlib.Path(d)
        (root / SERVICE).parent.mkdir(parents=True)
        (root / DASHBOARD).parent.mkdir(parents=True)

        # ① 两侧齐全 → 零缺行
        (root / SERVICE).write_text(_FIXTURE_SERVICE, encoding="utf-8")
        (root / DASHBOARD).write_text(_FIXTURE_DASHBOARD, encoding="utf-8")
        sup, staff = find_missing(root)
        if sup or staff:
            print(f"✗ S1 齐全样本误报: 超管缺 {sup} / 专员缺 {staff}")
            bad += 1

        # ② 超管视角缺一行（member_follow_up 从 TODO_ROWS 拿掉）→ 只该报超管缺
        (root / DASHBOARD).write_text(
            _FIXTURE_DASHBOARD.replace('  { key: "member_follow_up", label: "会员跟进" },\n', ""),
            encoding="utf-8",
        )
        sup, staff = find_missing(root)
        if sup != ["member_follow_up"] or staff:
            print(f"✗ S1 超管缺行未按视角检出: 超管 {sup} / 专员 {staff}")
            bad += 1

        # ③ 专员视角缺一行 → 只该报专员缺（**"从专员表挪进超管表"这一幕**）
        (root / DASHBOARD).write_text(
            _FIXTURE_DASHBOARD.replace(
                '  { key: "order_pending_manual", label: "待确认收款" },\n', ""
            ),
            encoding="utf-8",
        )
        sup, staff = find_missing(root)
        if sup or staff != ["order_pending_manual"]:
            print(f"✗ S1 专员缺行未按视角检出: 超管 {sup} / 专员 {staff}")
            bad += 1

        # ④ 锚点被改写 → 必须显式红（不允许静默降级成并集判法）
        (root / SERVICE).write_text(
            _FIXTURE_SERVICE.replace(SUPER_GATE, "if True:"), encoding="utf-8"
        )
        try:
            find_missing(root)
        except SystemExit:
            pass
        else:
            print("✗ S1 锚点被改写时未显式失败（静默降级 = P2-19 的假绿形状）")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（两侧齐全零误报 / 两视角缺行各自命中 / 锚点丢失显式红）")
    return 0


def main_against(root: pathlib.Path | None = None) -> int:
    """对指定根目录跑一次检查（`main()` 的 root 参数化版本——回归用例直接调它）。"""
    super_keys, staff_keys = admin_total_keys_by_viewpoint(root)
    rows = frontend_row_keys(root)
    super_missing, staff_missing = find_missing(root)
    print(
        f"[i] admin_total 求和项：超管视角 {len(super_keys)} 个 / 专员视角 {len(staff_keys)} 个；"
        f"仪表盘行：TODO_ROWS {len(rows['TODO_ROWS'])} 个 / STAFF_TODO_ROWS {len(rows['STAFF_TODO_ROWS'])} 个"
    )
    if super_missing or staff_missing:
        print(
            "[FAIL] 后端计入 admin_total 但对应视角的待办卡没有行（该视角会显示绿色"
            "「全部处理完毕」而侧边栏徽标 > 0）："
        )
        if super_missing:
            print("  · 超管视角（TODO_ROWS 缺行；专员卡不受影响）：")
            for k in super_missing:
                print(f"      - {k}")
        if staff_missing:
            print("  · 专员视角（STAFF_TODO_ROWS 缺行——含「从专员表挪进超管表」这一幕）：")
            for k in staff_missing:
                print(f"      - {k}")
        return 1
    print("[OK] 两个视角的待办卡行各自覆盖本视角 admin_total 全部求和项")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return main_against(ROOT)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    sys.exit(main())
