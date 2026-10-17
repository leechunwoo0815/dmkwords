# tests/unit/test_check_admin_todo_rows.py — 仪表盘待办卡对账检查器的反例固化（P1 #10 / P2-19）
"""检查器必须真的会红（否则门禁那一步只是摆设）——三向都钉住：

① 后端新增 `admin_total` 求和项、对应视角的卡片没加行 → 必须 FAIL：这正是实测到的假绿形状
   （卡片绿色「全部处理完毕」而侧边栏徽标 > 0）；
② **键从专员表挪进超管表**（并集不变）→ 必须 FAIL：P2-19 的原始缺陷（并集判法对此永远绿）；
③ 两边对齐 → PASS。

另钉住**解析口径**：`+=` 形态（含跨多行括号）的求和项也要被认出来——只认 `sum(...)` 元组
会漏掉 `counts["admin_total"] += (counts[a] + counts[b])` 这类今日真实存在的写法；
以及**按视角归位**（超管块 / `member.manage` 块），判据集中在 `checker.find_missing`。
"""

from __future__ import annotations

import pathlib

from scripts import check_admin_todo_rows as checker

BACKEND_TMPL = """
def todo_counts(self):
    counts = {k: 0 for k in ("a", "b")}
    if admin.role == AdminUser.ROLE_SUPER_ADMIN:
        counts["admin_total"] = sum(
            counts[k]
            for k in (
                "refund_pending",
                "withdrawal_pending",
            )
        )
    if role_has_permission(admin.role, "member.manage"):
        counts["admin_total"] += counts["member_follow_up"]
        counts["admin_total"] += (
            counts["pending_evaluation_followup"] + counts["deposit_settle_followup"]
        )
    __EXTRA__
    return counts
"""

FRONTEND_TMPL = """
const TODO_ROWS: { key: keyof TodoCounts; label: string; route: string }[] = [
  { key: "refund_pending", label: "待审退款", route: "/refund-center" },
  { key: "withdrawal_pending", label: "待审退会", route: "/refund-center" },
];

const STAFF_TODO_ROWS: { key: keyof TodoCounts; label: string; route: string }[] = [
  { key: "member_follow_up", label: "入会跟进", route: "/members" },
  { key: "pending_evaluation_followup", label: "待评估跟进", route: "/notifications" },
  { key: "deposit_settle_followup", label: "待结清跟进", route: "/notifications" },
];
"""


def _tree(tmp_path: pathlib.Path, extra: str = "", frontend: str | None = None) -> pathlib.Path:
    (tmp_path / "backend/domain/admin").mkdir(parents=True)
    (tmp_path / "backend/domain/admin/todo_service.py").write_text(
        BACKEND_TMPL.replace("__EXTRA__", extra), encoding="utf-8"
    )
    (tmp_path / "admin-web/src/pages").mkdir(parents=True)
    (tmp_path / "admin-web/src/pages/Dashboard.tsx").write_text(
        frontend or FRONTEND_TMPL, encoding="utf-8"
    )
    return tmp_path


def test_missing_row_is_flagged(tmp_path, monkeypatch, capsys):
    """后端多一个求和项（资金异常）而仪表盘没这一行 → 必须报错并点名该键。"""
    root = _tree(tmp_path, extra='counts["admin_total"] += counts["payment_anomaly"]')
    monkeypatch.setattr(checker, "ROOT", root)
    assert checker.main([]) == 1
    out = capsys.readouterr().out
    assert "payment_anomaly" in out and "[FAIL]" in out
    # 该行写在 `member.manage` 块内（两种角色都会走到）→ 归入**专员视角**
    _super_missing, staff_missing = checker.find_missing(root)
    assert staff_missing == ["payment_anomaly"]


def test_row_moved_across_tables_is_flagged(tmp_path, capsys):
    """P2-19 原形：键从专员表**挪进**超管表 → 并集不变，但专员卡漏行，必须 FAIL。

    这是 2026-10-15 P1 #10 那场假绿的原样复发路径：老判法（并集判超集）在这里恒绿。
    """
    moved = FRONTEND_TMPL.replace(
        '  { key: "member_follow_up", label: "入会跟进", route: "/members" },\n', "", 1
    ).replace(
        '  { key: "withdrawal_pending", label: "待审退会", route: "/refund-center" },\n',
        '  { key: "withdrawal_pending", label: "待审退会", route: "/refund-center" },\n'
        '  { key: "member_follow_up", label: "入会跟进", route: "/members" },\n',
        1,
    )
    root = _tree(tmp_path, frontend=moved)
    super_missing, staff_missing = checker.find_missing(root)
    assert staff_missing == ["member_follow_up"], "专员视角必须点名缺行"
    assert super_missing == [], "超管视角没漏（那一行挪到了 TODO_ROWS）"
    assert checker.main_against(root) == 1
    out = capsys.readouterr().out
    assert "member_follow_up" in out and "专员视角" in out


def test_aligned_passes(tmp_path, monkeypatch, capsys):
    """对齐 → 0；且 `+= (...) ` 跨行的两个键都被认出来（不然第 ① 条会假红）。"""
    root = _tree(tmp_path)
    monkeypatch.setattr(checker, "ROOT", root)
    assert checker.main([]) == 0
    out = capsys.readouterr().out
    assert "[OK]" in out
    super_keys, staff_keys = checker.admin_total_keys_by_viewpoint(root)
    assert super_keys == {"refund_pending", "withdrawal_pending"}
    assert staff_keys == {
        "member_follow_up",
        "pending_evaluation_followup",
        "deposit_settle_followup",
    }
