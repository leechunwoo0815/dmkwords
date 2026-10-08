# tests/unit/test_check_docs_code_alignment.py — 文档一致性检查器的反例固化（WM12-C 审查 P3-17）
"""P3-17 的结论：`b8c7047` 把符号正则从 `def|class` 放宽为 `(?:async\\s+)?(?:def|class)`——
这是**放宽**（文档引用已删的同步函数、而同名 async 函数存在于别处时会被放过），
当时把这次改动说成"收紧"与事实相反。两条语义在此钉住：

① 真不存在（连同名 async 都没有）→ 必须报断链：检查器本身有效（不是摆设）；
② 只剩同名 async（同步版已删）→ 放行：这是**已确认口径**（按名字判定落点，不区分 async/sync）。
   放宽的代价就是 ① 的反例被放过；收益是网关层 async 方法（微信支付/短信全是 async）
   被文档引用时不再假红——修复前 `refresh_platform_cert()` 这类引用会被误判"函数不存在"。
"""

from __future__ import annotations

import pathlib

from scripts import check_docs_code_alignment as checker


def _make_tree(tmp_path: pathlib.Path, doc_line: str, code: str) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text(doc_line + "\n", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "m.py").write_text(code, encoding="utf-8")
    # 检查器要数 `backend/domain` 下的域目录（数量断言用）——临时树里也得有这一层
    (tmp_path / "backend" / "domain").mkdir()
    (tmp_path / "scripts").mkdir()


def test_missing_symbol_is_flagged(tmp_path, monkeypatch, capsys):
    """符号真不存在 → 检查器必须报断链（否则门禁那一步就是摆设）。"""
    _make_tree(
        tmp_path,
        "文档写：调用 `deleted_helper()` 完成某事。",
        "def other_helper():\n    pass\n",
    )
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    assert checker.main() == 1
    out = capsys.readouterr().out
    assert "deleted_helper" in out and "函数/类不存在" in out


def test_same_name_async_is_accepted_documented_widening(tmp_path, monkeypatch, capsys):
    """只剩同名 async（同步版已删）→ 放行：已确认口径（async/sync 不区分，见模块 docstring）。"""
    _make_tree(
        tmp_path,
        "文档写：调用 `fetch_cert()` 刷新证书。",
        "async def fetch_cert():\n    pass\n",
    )
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    assert checker.main() == 0
    out = capsys.readouterr().out
    assert "fetch_cert" not in out
