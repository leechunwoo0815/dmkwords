# scripts/check_gate_selftest.py — 检查器自证清单元检查（第八轮审查 P1-10 ⑤）
"""**门禁的守护能力必须能被自身验证。**

背景（第八轮全量审查 P1-10，实测✓）：`gate.sh` 第 [5] 步注释里写着"含 S1 注入自检"，
**实际没传 `--self-test`**；子代理逐条变异实测（已还原）：

| 变异 | gate 调用方式的结果 |
|---|---|
| 把 M2 的 `?v=` 改坏（`check_media_discipline`） | 仍 `PASS`（只有手工加 `--self-test` 才红） |
| 把"文件存在性"判断改成 `if False and ...`（`check_docs_code_alignment`） | 仍打印"✓ 文档引用全部落到真实文件" |
| 关掉「禁用嵌入式数据库」规则（`verify_architecture`） | 仍 `架构关 PASS` |
| 关掉未声明码硬失败（`check_rbac_consistency`） | 仍打印"三方一致" |
| 让 R14 永远返回 `[]`（`check_miniapp_style`） | 仍 `PASS` |

"改了检查器没人知道"是本项目最贵的失败模式（历史事故：脚本中途断言失败导致端点丢失两天）。
本检查器把"自证清单"机械化，钉住三件事：

1. **gate 里跑的每个检查器都必须支持 `--self-test`**（不支持即红）；
2. **gate 必须真的传 `--self-test`**（注释说自证、代码不传 = 假自证）；
3. **仓库里每个检查器要么接进 gate，要么在白名单里写明理由**——新增检查器忘了接进 gate
   是同一族缺陷（第七轮 P2-6 实测过：110 个写端点只有 6 处挂限流）。

本检查器自身也必须支持 `--self-test`（它在 gate 的清单里）——自证用夹具 gate.sh + 夹具检查器。
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: gate.sh 里"检查器调用"的形状：`python -m scripts.<name>` 或 `python scripts/<name>.py`
INVOKE_RE = re.compile(r"python(?:3)?\s+(?:-m\s+scripts\.(\w+)|scripts/(\w+)\.py)")

#: 检查器文件名形状（要被本检查器管住的全集）
CHECKER_GLOB = ("check_*.py", "verify_*.py")

#: **不在 gate 里跑**的检查器白名单——必须写明理由（有据可查，禁止静默）
#: 键 = 脚本名去掉 .py（与 `repo_checkers()` 同口径）
NOT_IN_GATE: dict[str, str] = {
    "check_wechat_credentials": "离线凭据自检（需真实 AppID/商户号），按 docs/21 手工执行",
}

#: **运行期检查器**：被 gate 调用，但不存在可注入的纯函数自证（它们操作真实环境/真实库）。
#: R11-e S4 起 `check_demo_baseline` 在门禁收尾恢复后执行；它的"自证"就是真实计数比对本身。
RUNTIME_CHECKERS: dict[str, str] = {
    "check_demo_baseline": "起停 dev + 查真实库的现场基线核验；无纯函数自证面（门禁价值=真实计数）",
}

#: **gate 文本里出现、但当前不执行**的旧结构工具（`if [ -f ... ]` 守卫，F0 重写后启用）。
#: R11-e S5（2026-10-26）：原先把这两个名字算进"gate 调用 16 个"，制造了不存在的覆盖面。
PENDING_TOOLS: dict[str, str] = {
    "verify_api_contract": "旧结构工具，gate 用 `-f` 守卫；F0 重写后启用",
    "check_model_consistency": "旧结构工具，gate 用 `-f` 守卫；F0 重写后启用",
}


def _code_lines(text: str) -> str:
    """去掉整行注释——gate.sh 注释里会提到并不执行的脚本名（S5 假计数的根源之一）。"""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def gate_invocations(gate: pathlib.Path) -> set[str]:
    """gate.sh 里**实际出现调用**的脚本名（不含 .py；注释行不算）。"""
    text = _code_lines(gate.read_text(encoding="utf-8"))
    return {m.group(1) or m.group(2) for m in INVOKE_RE.finditer(text)}


def gate_checker_names(gate: pathlib.Path) -> set[str]:
    """gate 实际调用的**检查器**（排除 `-f` 守卫的待启用旧工具与导出脚本）。"""
    return {
        name
        for name in gate_invocations(gate)
        if name not in PENDING_TOOLS and (name.startswith("check_") or name.startswith("verify_"))
    }


def repo_checkers(root: pathlib.Path) -> set[str]:
    out: set[str] = set()
    for pat in CHECKER_GLOB:
        out |= {p.stem for p in (root / "scripts").glob(pat)}
    return out


def find_problems(root: pathlib.Path) -> list[str]:
    """判定只在这一处（main 与自证共用）。"""
    gate = root / "scripts/gate.sh"
    if not gate.is_file():
        return ["scripts/gate.sh 不存在——本检查无对象"]
    problems: list[str] = []
    invoked = gate_invocations(gate)
    active = invoked - set(PENDING_TOOLS)
    gate_text = gate.read_text(encoding="utf-8")

    # ① gate 真的传了 --self-test（注释写自证、代码不传 = P1-10 的原形状）
    if "--self-test" not in gate_text:
        problems.append("gate.sh 全文没有 `--self-test`——检查器自证没有接入门禁（P1-10 原形状）")

    # ② 实际调用必须有实体文件（R11-e S5：原先把 `-f` 守卫的旧工具/注释里的名字也算进覆盖数）
    for name in sorted(active):
        if not (root / "scripts" / f"{name}.py").is_file():
            problems.append(
                f"scripts/gate.sh 调用了不存在的脚本 scripts/{name}.py——"
                "门禁会静默少跑一条（注释里的提及不算调用；确属待启用工具请写进 PENDING_TOOLS）"
            )

    # ③ gate 里跑的每个**检查器**都必须支持 --self-test（非检查器脚本不在本检查范围；
    #    运行期检查器见 RUNTIME_CHECKERS——它们操作真实环境，没有可注入的纯函数自证面）
    for name in sorted(active):
        if not (name.startswith("check_") or name.startswith("verify_")):
            continue  # 契约导出/迁移等非检查器脚本
        if name in RUNTIME_CHECKERS:
            continue
        f = root / "scripts" / f"{name}.py"
        if f.is_file() and "--self-test" not in f.read_text(encoding="utf-8"):
            problems.append(
                f"scripts/{name}.py 被 gate 调用但**不支持 `--self-test`**——"
                "它改坏了没人知道（补自证，或把它从 gate 移除并写进 NOT_IN_GATE）"
            )

    # ④ 仓库里的每个检查器要么接进 gate、要么白名单里有理由
    for name in sorted(repo_checkers(root)):
        if name in active or name in NOT_IN_GATE:
            continue
        problems.append(
            f"scripts/{name}.py **没有接进 gate**（新增检查器必须进 CHECKER_CMDS；"
            "确实不该进的写进本文件 NOT_IN_GATE 并注明理由）"
        )
    return problems


def self_test() -> int:
    """S1 注入自检：三类违例必须各命中一条，合规样本不得误报。"""
    bad = 0
    with tempfile.TemporaryDirectory(prefix="gate-selftest-") as d:
        root = pathlib.Path(d)
        (root / "scripts").mkdir(parents=True)

        def write_gate(body: str) -> None:
            (root / "scripts/gate.sh").write_text(body, encoding="utf-8")

        def write_checker(name: str, *, selftest: bool) -> None:
            tail = (
                'if __name__ == "__main__":\n'
                '    if "--self-test" in sys.argv:\n'
                "        raise SystemExit(0)\n"
                if selftest
                else 'if __name__ == "__main__":\n    raise SystemExit(0)\n'
            )
            (root / "scripts" / f"{name}.py").write_text("import sys\n" + tail, encoding="utf-8")

        # ① 合规样本：唯一检查器接进 gate 且支持自证 → 零问题
        write_gate(
            "run python -m scripts.check_alpha --self-test\nrun python -m scripts.check_alpha\n"
        )
        write_checker("check_alpha", selftest=True)
        if find_problems(root):
            print(f"✗ S1 合规样本误报: {find_problems(root)}")
            bad += 1

        # ② 违例：被 gate 调用但不支持 --self-test
        write_checker("check_beta", selftest=False)
        write_gate(
            "run python -m scripts.check_alpha --self-test\nrun python -m scripts.check_beta\n"
        )
        got = find_problems(root)
        if not any("check_beta" in p and "--self-test" in p for p in got):
            print(f"✗ S1 「被调用但无自证」未命中: {got}")
            bad += 1

        # ③ 违例：gate 全文没有 --self-test（假自证形状）
        write_gate("run python -m scripts.check_alpha\n")
        write_checker("check_alpha", selftest=True)
        got = find_problems(root)
        if not any("没有 `--self-test`" in p for p in got):
            print(f"✗ S1 「gate 未传自证」未命中: {got}")
            bad += 1

        # ④ 违例：仓库里有检查器没接进 gate
        write_gate("run python -m scripts.check_alpha --self-test\n")
        write_checker("check_orphan", selftest=True)
        got = find_problems(root)
        if not any("check_orphan" in p for p in got):
            print(f"✗ S1 「检查器未接入 gate」未命中: {got}")
            bad += 1

        # ⑤ 违例：gate 调用不存在的脚本（R11-e S5 的"假覆盖"形状）
        write_gate(
            "run python -m scripts.check_alpha --self-test\nrun python -m scripts.check_ghost\n"
        )
        got = find_problems(root)
        if not any("check_ghost" in p and "不存在" in p for p in got):
            print(f"✗ S1 「调用不存在的检查器」未命中: {got}")
            bad += 1
    if bad:
        print(f"✗ S1 注入自检失败 {bad} 项")
        return 1
    print("✓ S1 注入自检通过（无自证/假自证/未接入/调用不存在四类违例全命中 + 合规样本零误报）")
    return 0


def main() -> int:
    problems = find_problems(ROOT)
    if problems:
        print(f"[gate-selftest] ✗ 检查器自证清单不一致（{len(problems)} 处）：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    gate = ROOT / "scripts/gate.sh"
    invoked = sorted(gate_invocations(gate))
    checkers = sorted(gate_checker_names(gate))
    pending = sorted(set(invoked) & set(PENDING_TOOLS))
    noncheckers = sorted(set(invoked) - set(checkers) - set(pending))
    runtime = sorted(set(checkers) & set(RUNTIME_CHECKERS))
    repo = sorted(repo_checkers(ROOT))
    print(
        f"[gate-selftest] ✓ 检查器自证清单一致：仓库检查器 {len(repo)} 个 | "
        f"gate 调用检查器 {len(checkers)} 个（支持 --self-test；运行期 {len(runtime)} 个除外）| "
        f"非检查器脚本 {len(noncheckers)} 个 | "
        f"`-f` 守卫待启用 {len(pending)} 个 | 白名单 {len(NOT_IN_GATE)} 个（已注明理由）"
    )
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
