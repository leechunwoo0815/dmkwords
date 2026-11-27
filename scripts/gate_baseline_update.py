# scripts/gate_baseline_update.py — 单测数量基线的**自动回写**（B5-1，2026-11-27）
"""为什么要有这个脚本：`min_passed` 原先是手工维护的，于是它与真实收集数持续漂移——
2026-11-27 实测：基线 1337，实际收集 1455，**空档 118 个**。这 118 个空档意味着
"删掉 118 个测试仍然全绿"，等于基线形同虚设。

规则（三条缺一不可）：
1. **只升不降**：本次收集数 > 基线才回写；低于基线时门禁那一侧已经判红，本脚本绝不"跟着下调"
   （自动下调 = 把删测洗成绿色，比不写更糟）。
2. **相等即不落盘**：值没变就不动文件（连 `updated_at` 都不动），否则 CI 的
   `git diff --exit-code` 每轮必红。
3. **`updated_at` 只在真回写时更新**，与 `last_collected` 同源，便于审计"上次是什么时候抬的线"。

CI 侧配套（B5-1 后半句"CI 校验两者相等"）：gate 跑完后 `git diff --exit-code -- scripts/gate_baseline.json`
——若 CI 的收集数高于仓库里的基线，本脚本会在 CI 工作区抬高它 → diff 非空 → 判红，
提示"本地跑一次门禁并把基线提交上来"。这比让 CI 静默接受一个过期基线更可靠：
基线值从此**只能**来自一次真实的全量门禁。

用法：
    python scripts/gate_baseline_update.py 1455     # 回写（只升不降）
    python scripts/gate_baseline_update.py --show    # 读当前基线（诊断用）
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "gate_baseline.json"


def update(collected: int, path: Path = BASELINE, today: str | None = None) -> bool:
    """把收集数回写进基线；返回是否真的改动了文件（只升不降 + 相等不落盘）。"""
    if collected <= 0:
        raise ValueError(f"收集数必须为正整数：{collected}")
    data = json.loads(path.read_text(encoding="utf-8"))
    old = int(data["min_passed"])
    if collected < old:
        # 门禁主流程已经因为"低于基线"判红；这里只解释为何不写。
        print(
            f"单测数量基线未回写：本次收集 {collected} < 基线 {old}"
            "（只升不降——自动下调会把删测洗成绿色）"
        )
        return False
    if collected == old:
        print(f"单测数量基线与本次收集一致（{old}），无需回写")
        return False
    data["min_passed"] = collected
    data["updated_at"] = today or date.today().isoformat()
    data["last_collected"] = collected
    # 保持原有键序 + 2 空格缩进 + 结尾换行（与仓库其余 json 一致，避免无意义 diff）
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"单测数量基线已回写：{old} → {collected}（只升不降；请随本批提交一起入库）")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    if argv[1] in {"--show", "-s"}:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        print(f"min_passed={data['min_passed']} updated_at={data.get('updated_at')}")
        return 0
    try:
        collected = int(argv[1])
    except ValueError:
        print(f"收集数必须是整数：{argv[1]!r}")
        return 2
    update(collected)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
