#!/usr/bin/env python
"""门禁 skip 审计（W8-D11，2026-10-28 B4 批）。

为什么需要：门禁原先只核对"收集数 ≥ 基线"，**skip 本身不受约束**——把一条测试改成
`pytest.skip()` 或加个环境判断，收集数不变、门禁照绿，而那条判据从此不再执行。
本脚本解析 `pytest -rs` 的 `SKIPPED [n] path:line: reason` 行，要求每个被跳过的**文件**
都在 `scripts/gate_baseline.json` 的 `allowed_skips` 里登记过理由；未登记即判红。

用法：
    python scripts/gate_skip_audit.py /tmp/pytest.out
    python scripts/gate_skip_audit.py --self-test

退出码：0=全部已登记 / 1=有未登记 skip 或输出缺失。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "gate_baseline.json"

SKIP_LINE_RE = re.compile(r"^SKIPPED\s+\[\d+\]\s+(?P<file>[^:\n]+):\d+:")


def parse_skipped_files(text: str) -> list[str]:
    """从 pytest 输出里提取被跳过的测试文件（去重排序）。"""
    files = set()
    for line in text.splitlines():
        m = SKIP_LINE_RE.match(line.strip())
        if m:
            files.add(m.group("file").strip())
    return sorted(files)


def audit(text: str, allowed: dict[str, str]) -> tuple[list[str], list[str]]:
    """返回 (已登记文件, 未登记文件)。"""
    skipped = parse_skipped_files(text)
    ok = [f for f in skipped if f in allowed]
    bad = [f for f in skipped if f not in allowed]
    return ok, bad


def self_test() -> int:
    bad = 0
    allowed = {"tests/unit/test_media_health.py": "时钟分支"}
    sample = (
        "SKIPPED [1] tests/unit/test_media_health.py:426: 本地时间未过 08:00\n"
        "SKIPPED [2] tests/unit/test_x.py:10: node 不可用\n"
        "1 passed, 2 skipped in 0.1s\n"
        "SKIPPED [1] tests/unit/test_media_health.py:426: 本地时间未过 08:00\n"
    )
    ok, bad_files = audit(sample, allowed)
    if ok != ["tests/unit/test_media_health.py"]:
        print(f"✗ 已登记文件解析错误：{ok}")
        bad += 1
    if bad_files != ["tests/unit/test_x.py"]:
        print(f"✗ 未登记文件解析错误：{bad_files}")
        bad += 1
    if audit("3 passed in 0.1s\n", allowed) != ([], []):
        print("✗ 无 skip 时应返回空")
        bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（已登记/未登记/无 skip 三种样本）")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if len(argv) < 2:
        print("用法：python scripts/gate_skip_audit.py <pytest 输出文件>")
        return 2
    text = pathlib.Path(argv[1]).read_text(encoding="utf-8", errors="ignore")
    allowed = json.loads(BASELINE.read_text(encoding="utf-8")).get("allowed_skips", {})
    ok, bad = audit(text, allowed)
    if bad:
        print(f"✗ 出现未登记的 skip（{len(bad)} 个文件）：")
        for f in bad:
            print(f"   {f}")
        print("  确属环境性跳过请在 scripts/gate_baseline.json 的 allowed_skips 登记文件 + 理由")
        return 1
    print(f"✓ skip 判据 OK：{len(ok)} 个文件全部已登记（{', '.join(ok) or '无 skip'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
