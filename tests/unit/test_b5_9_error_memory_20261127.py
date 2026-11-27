# tests/unit/test_b5_9_error_memory_20261127.py — 错误记忆库结构与入库判据（B5-9）
"""审查① G4：库停在 10-31，11 月新坑一条没入；开工指针指向 8-9 月内容。

本批三件事，都必须可机判（否则下个会话又会停在"库太大，回头再补"）：
  ① 结构：库顶部有 **§零「滚动最新 10 条」**，且这 10 节**恒为编号最高的 10 节**；
  ② 补录：11 月四族（控制台表单≠代码 / envVersion / WXML 条件链 / CI 并发取消）已成节；
  ③ 入库判据：LEDGER 里带「教训/根因/新坑」的批次行（≥ 第 191 批）必须在库里提到同一批次；
     检查器已接进 gate（`CHECKER_CMDS`），并支持 `--self-test`（写坏检查器必须报得出来）。

`scripts/` 文本断言面与门禁其余检查器同口径（脚本无 Python 运行时依赖，只读文本）。
"""

from __future__ import annotations

from pathlib import Path

from scripts import check_error_memory as cem
from scripts.check_gate_selftest import gate_invocations

ROOT = Path(__file__).resolve().parent.parent.parent
GATE = ROOT / "scripts/gate.sh"
AGENTS = ROOT / "AGENTS.md"

# 用**字面路径**读文档产物（不是 `latest_memory_file()`）：`check_fake_assertions` 的
# 源码文本断言登记制要求读取目标可静态判定；下面 `test_literal_path_is_the_newest_file`
# 保证这个字面路径永远等于 glob 取到的最新库——硬编码不会悄悄过期。
MEM_LIB = ROOT / "error_list" / "错误记忆库-全项目-20260831.md"
LEDGER = ROOT / "docs" / "LEDGER.md"


def test_literal_path_is_the_newest_file():
    """字面路径守卫：本文件里的 MEM_LIB 必须仍是 glob 取到的最新记忆库（否则断言会测旧文件）。"""
    assert cem.latest_memory_file() == MEM_LIB


def test_rolling_index_is_the_newest_ten_sections():
    """§零 的 10 行必须等于文件里编号最高的 10 节（落后一条就判红）。"""
    mem = MEM_LIB.read_text(encoding="utf-8")
    assert cem.check_memory(mem) == [], "滚动索引与真实最新 10 节不一致"

    rows = cem.rolling_rows(mem)
    assert rows is not None and len(rows) == cem.ROLLING_SIZE
    rolled = [cem.cn_to_int(r["section"]) for r in rows]
    newest = sorted(cem.headings(mem), reverse=True)[: cem.ROLLING_SIZE]
    assert rolled == newest, f"索引 {rolled} ≠ 最新 {newest}"
    assert rolled == sorted(rolled, reverse=True), "索引必须新→旧排序"


def test_november_root_cause_families_are_recorded():
    """11 月四族必须真的成为正文（不是只在滚动索引里挂个名字）。"""
    text = MEM_LIB.read_text(encoding="utf-8")
    newest = max(cem.headings(text))
    assert newest >= 149, f"最新节号 {newest} < 149——11 月族补录没成功入库"
    # 四族的判别词（命中族名/真因即可，避免把措辞钉死）
    for needle in ("控制台表单", "envVersion", "wx:else", "cancel-in-progress"):
        assert needle in text, f"11 月族补录缺内容：{needle}"
    # 复发项也要在（同族第二次犯比首次更值得记）
    assert "复发" in text


def test_ledger_lessons_must_be_recorded():
    """LEDGER 的「教训/根因」批次行（≥ 第 191 批）必须在本库出现同一批次。"""
    ledger = LEDGER.read_text(encoding="utf-8")
    mem = MEM_LIB.read_text(encoding="utf-8")
    assert cem.check_ledger(ledger, mem) == [], "LEDGER 有新批教训未写入错误记忆库"


def test_checker_wired_into_gate_and_supports_self_test():
    """新增检查器忘了接进 gate = 同一个"改了没人知道"的族（check_gate_selftest 的纪律）。"""
    assert "check_error_memory" in gate_invocations(GATE), "检查器未接进 gate.sh"
    src = (ROOT / "scripts/check_error_memory.py").read_text(encoding="utf-8")
    assert '"--self-test" in sys.argv' in src, "检查器不支持 --self-test（门禁会判红）"
    assert cem.self_check() == 0, "检查器自证失败"


def test_open_work_pointer_targets_the_rolling_section():
    """开工指针必须指向 §零（G4 的原症状：指针指向三个月前的内容，等于没有指针）。"""
    text = AGENTS.read_text(encoding="utf-8")
    assert "滚动最新 10 条" in text, "AGENTS.md 开工指针未指向 §零 滚动段"
