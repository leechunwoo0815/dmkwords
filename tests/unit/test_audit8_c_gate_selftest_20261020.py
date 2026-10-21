# tests/unit/test_audit8_c_gate_selftest_20261020.py — 第八轮审查 C 批：门禁自证（P1-10）
"""**缺陷（第八轮审查 P1-10，实测✓）**：门禁的"自证"是假的——`gate.sh` 第 [5] 步注释写着
"含 S1 注入自检"而**实际没传 `--self-test`**；`check_docs_code_alignment` /
`verify_architecture` / `check_rbac_consistency` / `check_fake_assertions` /
`check_miniapp_bindings` **根本没有自证**；`check_miniapp_style` 的 R14 自证被
`if not sv:` 缩进短路（夹具里注入了非法选择器 → `sv` 非空 → 整块从不执行）；
gate [6] 迁移目录为空时**静默 skip**。实测：把 `?v=` 改坏、关掉 sqlite 禁令、
让 R14 永远返回 `[]`、把"文件存在性"改成 `if False and ...` —— gate **全部照样 PASS**。

**本文件钉住两件事**：
1. **每个检查器都真的支持 `--self-test` 且能通过**（真子进程跑，与 gate 同命令）；
2. **自证是"活的"**——把该检查器的检测器**当场改坏**（monkeypatch 成空返回），
   它的 `self_test()` **必须报失败**。只断言"自证通过"是抓不到 R14 那种缩进短路的
   （死代码也"通过"）；必须能证伪才叫自证（红线 171 族）。
"""

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

#: gate.sh 第 [5] 步清单里**跑自证**的检查器（与 `CHECKER_CMDS` 同口径；
#: 清单一致性由 `check_gate_selftest` 机械校验，这里只跑"自证真的过"）
SELF_TESTED = [
    "check_fake_assertions",
    "check_bdd_baseline",  # 第八轮 P2-69（2026-10-21 补）：BDD 挂起基线守卫
    "check_miniapp_bindings",
    "check_miniapp_wiring",
    "check_docs_code_alignment",
    "check_rbac_consistency",
    "check_admin_todo_rows",
    "check_miniapp_style",
    "check_media_discipline",
    "check_copy_emoji",
    "check_gate_selftest",
    "verify_architecture",
]


def _load(name: str):
    """按文件路径加载 scripts/<name>.py（它们不是包，import 名不可用）。"""
    spec = importlib.util.spec_from_file_location(f"_probe_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_selftest(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), "--self-test"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=300,
    )


@pytest.mark.parametrize("name", SELF_TESTED)
def test_every_checker_has_working_selftest(name: str):
    """每个检查器都必须支持 `--self-test` 且退出 0（真子进程，与 gate 同调用形状）。"""
    r = _run_selftest(name)
    assert r.returncode == 0, (
        f"scripts/{name}.py --self-test 未通过（exit={r.returncode}）：\n{r.stdout[-800:]}\n{r.stderr[-400:]}"
    )
    assert "S1" in r.stdout or "自检" in r.stdout, f"自证输出不像自检：{r.stdout[-300:]}"


def _gate_checker_loop_commands() -> list[str]:
    """**真执行** gate.sh 的检查器清单循环（把 `run` 换成记录器），返回它会跑的每条命令。

    为什么不用"grep gate.sh 里有没有 `--self-test`"（源码字符串断言 = 假绿）：
    那样只能证明"这个字符串在文件里出现过"——把它从**循环体**里删掉、留在别处的注释里，
    断言照样通过（本用例首版就这么假绿过一次，变异实测 M2 GREEN）。真执行才判别得出
    "gate 到底会对每个检查器跑什么"。
    """
    text = (SCRIPTS / "gate.sh").read_text(encoding="utf-8")
    start = text.index("CHECKER_CMDS=(")
    end = text.index("done", start) + len("done")
    snippet = text[start:end]
    script = 'run() { printf "%s\\n" "$*"; }\n' + snippet
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, cwd=ROOT, timeout=60, check=True
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_gate_actually_passes_selftest_flag():
    """gate 必须对**每个**检查器先跑 `--self-test` 再跑主流程（真执行清单循环判别）。

    RED（还原 P1-10 原形状：循环体不传 `--self-test`）：记录到的命令里没有自证那一条。
    """
    cmds = _gate_checker_loop_commands()
    assert cmds, "gate.sh 的检查器清单循环没有产出任何命令（清单/循环被改坏）"
    selftests = [c for c in cmds if c.endswith("--self-test")]
    mains = [c for c in cmds if not c.endswith("--self-test")]
    assert len(selftests) == len(mains) == len(cmds) / 2, (
        f"每个检查器都该「自证 + 主流程」各一条，实得 自证 {len(selftests)} / 主流程 {len(mains)}"
    )
    for cmd in mains:
        assert f"{cmd} --self-test" in cmds, f"{cmd} 没有对应的自证调用（改了检查器没人知道）"


def test_selftest_clist_is_consistent():
    """自证清单元检查：仓库里的检查器要么接进 gate、要么白名单写明理由（零问题）。"""
    mod = _load("check_gate_selftest")
    problems = mod.find_problems(ROOT)
    assert problems == [], f"检查器自证清单不一致：{problems}"


# ---------------------------------------------------------------- 自证"有效性"（能证伪）


def test_style_selftest_detects_r14_short_circuit():
    """**R14 缩进短路的回归钉**：把 WXML 结构检测器改成永远返回空 → 自证必须报失败。

    RED（还原 P1-10 的原始形状：R14 自证被嵌在 `if not sv:` 里）：检测器改成空返回后
    自证**照样通过**（那两条断言是死代码）→ 本用例红。
    """
    mod = _load("check_miniapp_style")
    tokens, _ = mod.load_tokens()
    original = mod.scan_wxml_structure
    try:
        mod.scan_wxml_structure = lambda *a, **k: []
        fails = mod.self_test(tokens)
    finally:
        mod.scan_wxml_structure = original
    assert any("R14" in f or "WXML" in f for f in fails), (
        f"R14 检测器被改坏后自证必须报失败（说明自证是活的），实得 {fails}"
    )


def test_style_selftest_detects_selector_var_break():
    """R11（选择器位置 var()）自证同样必须是活的（它与 R14 曾共用同一个 `if not sv:`）。"""
    mod = _load("check_miniapp_style")
    tokens, _ = mod.load_tokens()
    original = mod.scan_selector_var
    try:
        mod.scan_selector_var = lambda *a, **k: []
        fails = mod.self_test(tokens)
    finally:
        mod.scan_selector_var = original
    assert any("选择器" in f for f in fails), f"R11 检测器改坏后自证必须报失败，实得 {fails}"


def test_docs_code_selftest_is_live():
    """文档悬空检查的自证必须能证伪：`scan_doc` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_docs_code_alignment")
    original = mod.scan_doc
    try:
        mod.scan_doc = lambda *a, **k: []
        rc = mod.self_test()
    finally:
        mod.scan_doc = original
    assert rc == 1, "scan_doc 被改坏后自证必须失败（否则自证是死的）"


def test_architecture_selftest_is_live():
    """架构关自证必须能证伪：把 sqlite 禁令改成空实现 → 自证报失败。"""
    mod = _load("verify_architecture")
    original = mod.check_sqlite_ban
    try:
        mod.check_sqlite_ban = lambda errors: None
        rc = mod.self_test()
    finally:
        mod.check_sqlite_ban = original
    assert rc == 1, "sqlite 禁令被改坏后架构关自证必须失败"


def test_fake_assertions_selftest_is_live():
    """假绿断言检查的自证必须能证伪：`scan` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_fake_assertions")
    original = mod.scan
    try:
        mod.scan = lambda *a, **k: ([], [])
        rc = mod.self_test()
    finally:
        mod.scan = original
    assert rc == 1, "扫描函数被改坏后自证必须失败"


def test_bdd_baseline_selftest_is_live():
    """BDD 挂起基线守卫的自证必须能证伪：`find_problems` 改成永远返回空 → 自证报失败。

    RED（还原 P2-69 原形状：无基线守卫）：没有这个检查器，本条无从谈起；
    检查器在但判定被改坏时，自证必须报「静默新增挂起」未命中。
    """
    mod = _load("check_bdd_baseline")
    original = mod.find_problems
    try:
        mod.find_problems = lambda *a, **k: []
        rc = mod.self_test()
    finally:
        mod.find_problems = original
    assert rc == 1, "挂起基线判定被改坏后自证必须失败（否则自证是死的）"


def test_miniapp_bindings_selftest_is_live():
    """数据面断链检查的自证必须能证伪：`missing_for` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_miniapp_bindings")
    original = mod.missing_for
    try:
        mod.missing_for = lambda *a, **k: []
        rc = mod.self_test()
    finally:
        mod.missing_for = original
    assert rc == 1, "断链判定被改坏后自证必须失败"


def test_admin_todo_rows_selftest_is_live():
    """待办行检查的自证必须能证伪：`find_missing` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_admin_todo_rows")
    original = mod.find_missing
    try:
        mod.find_missing = lambda *a, **k: ([], [])
        rc = mod.self_test()
    finally:
        mod.find_missing = original
    assert rc == 1, "缺行判定被改坏后自证必须失败"


def test_rbac_selftest_is_live():
    """RBAC 对账的自证必须能证伪：`backend_used` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_rbac_consistency")
    original = mod.backend_used
    try:
        mod.backend_used = lambda *a, **k: {}
        rc = mod.self_test()
    finally:
        mod.backend_used = original
    assert rc == 1, "后端引用抽取被改坏后自证必须失败"


def test_gate_selftest_meta_is_live():
    """自证清单元检查的自证必须能证伪：`find_problems` 改成永远返回空 → 自证报失败。"""
    mod = _load("check_gate_selftest")
    original = mod.find_problems
    try:
        mod.find_problems = lambda *a, **k: []
        rc = mod.self_test()
    finally:
        mod.find_problems = original
    assert rc == 1, "清单判定被改坏后自证必须失败"
