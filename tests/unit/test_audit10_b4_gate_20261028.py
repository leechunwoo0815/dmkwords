# tests/unit/test_audit10_b4_gate_20261028.py — 第十轮 B4：门禁/检查器自身判据
"""B4 批新增判据的常驻回归（W8-D2 / W8-D11 / W8-D16）。

- W8-D2：管理端 A1「失败吞成空」覆盖四种原漏形态；
- W8-D11：skip 审计只放过登记过的文件；
- W8-D16：RBAC 的"死权限 / 前端零拦截"必须有登记表兜底（行为由 check_rbac_consistency
  主流程验证；本文件只钉住登记表与解析函数的存在性语义）。
"""

from __future__ import annotations

from scripts import check_admin_failure_states as cafs
from scripts import check_rbac_consistency as rbac
from scripts import gate_skip_audit as gsa


def test_a1_swallow_forms_are_all_detected():
    """W8-D2：带参 / 块体 / 对象字面量 / 跨行四种吞空写法都必须命中。"""
    bad = (
        "apiX().catch((e) => []);\n",
        "apiX().catch(() => { setRows([]); });\n",
        "apiX().catch(() => ({ rows: [] }));\n",
        "apiX().catch(() =>\n  []);\n",
        "apiX().catch(() => setRows(null));\n",
    )
    for text in bad:
        assert cafs.scan_file(text), f"未命中吞空写法：{text!r}"


def test_a1_does_not_flag_real_failure_handling():
    """有失败提示的兜底不得误报（A1-ok / message / console / error 键）。"""
    good = (
        "apiX().catch(() => { setRows([]); message.error('加载失败'); });\n",
        "apiX().catch(() => ({ rows: [], error: '加载失败' }));\n",
        "apiX().catch((e) => { console.error(e); setRows([]); });\n",
        "apiX().catch(() => []); // A1-ok: 下拉兜底\n",
    )
    for text in good:
        assert not cafs.scan_file(text), f"误报合法写法：{text!r}"


def test_skip_audit_allows_registered_files_only():
    allowed = {"tests/unit/test_media_health.py": "时钟分支"}
    text = (
        "SKIPPED [1] tests/unit/test_media_health.py:426: 本地时间未过 08:00\n"
        "SKIPPED [2] tests/unit/test_unknown.py:10: 谁加的？\n"
    )
    ok, bad = gsa.audit(text, allowed)
    assert ok == ["tests/unit/test_media_health.py"]
    assert bad == ["tests/unit/test_unknown.py"]


def test_rbac_allowlists_have_reasons():
    """W8-D16：两张登记表都必须写理由（空理由 = 静默放行）。"""
    assert all(reason.strip() for reason in rbac.UNWIRED_ALLOWLIST.values())
    assert all(reason.strip() for reason in rbac.FRONTEND_ALLOWLIST.values())
    # 登记表只能用于"已声明"的码；目录里没有的必须在一次 run 后清掉
    from backend.domain.admin.service import all_permission_codes

    declared = set(all_permission_codes())
    stale = (set(rbac.UNWIRED_ALLOWLIST) | set(rbac.FRONTEND_ALLOWLIST)) - declared
    assert not stale, f"登记表含已不存在的权限码（僵尸条目）：{sorted(stale)}"
