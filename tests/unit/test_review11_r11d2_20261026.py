# tests/unit/test_review11_r11d2_20261026.py — 第十一轮整改 R11-d2（第一百五十四批）
"""管理端导出中文口径（A8）+ 审计标签后端副本完整性。

前端标签由 `test_audit8_p3_admin_labels_20261022.py` 钉住；本文件钉**后端导出**用的那张表
（`backend/domain/admin/audit_labels.py`）必须覆盖后端的全部动作/对象字面量——
否则导出会回落到英文枚举（A8 的原始症状）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.domain.admin.audit_labels import AUDIT_ACTION_LABEL, AUDIT_TARGET_LABEL
from backend.domain.circulation.records_service import _CONDITION_LABEL, _STATUS_LABEL

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _literals(kwarg: str) -> set[str]:
    pat = re.compile(rf'{kwarg}="([a-z][a-z0-9_.]*)"')
    found: set[str] = set()
    for py in BACKEND.rglob("*.py"):
        found |= set(pat.findall(py.read_text(encoding="utf-8")))
    return found


def _positional_audit_actions() -> set[str]:
    found: set[str] = set()
    for py in BACKEND.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "_audit":
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if re.fullmatch(r"[a-z][a-z0-9_.]*", arg.value):
                    found.add(arg.value)
    return found


def test_export_action_labels_cover_backend_literals():
    backend = _literals("action") | _positional_audit_actions()
    missing = sorted(backend - set(AUDIT_ACTION_LABEL))
    assert not missing, f"导出会回落英文：{missing}（同步 AuditLog.tsx 与 audit_labels.py）"


def test_export_target_labels_cover_backend_literals():
    backend = _literals("target_type")
    missing = sorted(backend - set(AUDIT_TARGET_LABEL))
    assert not missing, f"导出会回落英文：{missing}"


def test_borrow_export_labels_cover_enums():
    assert set(_STATUS_LABEL) == {"active", "overdue", "returned", "lost"}
    assert set(_CONDITION_LABEL) == {"normal", "maintenance", "lost"}
