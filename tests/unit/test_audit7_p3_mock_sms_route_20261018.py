# tests/unit/test_audit7_p3_mock_sms_route_20261018.py — 第七轮 P3-6：mock 短信路由分层合规
"""P3-6：`mock_routes.py` 未挂载（死代码），但里面留着一个 `HTTPException` —— 一旦按开关启用
就带病上线（Router 抛 HTTPException 违反宪法 §三）。修法：改抛业务异常 `NotFoundError`
（全局处理器映射 404，行为不变），并**保住"本地取验证码"这个用户裁定要保留的功能**。

RED（改回 `HTTPException`）：① 直接调用抛的异常类型不对；② 源码里又出现 `HTTPException`。
"""

from __future__ import annotations

import pathlib

import pytest

from backend.common.exceptions import NotFoundError


def test_get_code_raises_business_exception_not_httpexception():
    """无验证码时必须抛业务异常（全局处理器 → 404），不是 HTTPException。"""
    from backend.common.gateways.sms.mock_routes import get_sms_code

    with pytest.raises(NotFoundError):
        get_sms_code("13900000000", admin=None)


def test_module_has_no_httpexception():
    """源码级守护（**AST**，不看注释/docstring）：模块不得导入或使用 `HTTPException`。

    为什么用 AST 而不是 `"HTTPException" not in src`：本模块的 docstring 正**解释**这次改动，
    必然提到这个类名——纯文本断言会把"说明"当成"违规"（初版就是这么红的，如实记录）。
    """
    import ast

    src = (
        pathlib.Path(__file__).resolve().parents[2] / "backend/common/gateways/sms/mock_routes.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "HTTPException" not in (used | imported), "Router 不得导入/抛 HTTPException（宪法 §三）"
    assert "NotFoundError" in (used | imported)
