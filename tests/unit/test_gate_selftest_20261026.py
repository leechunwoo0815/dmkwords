# tests/unit/test_gate_selftest_20261026.py — R11-e S1–S6 门禁自证补强判据
"""第十一轮 R11-e（S1–S6）的机器判据（docs/审查报告-第十一轮-20261025.md §3.7）。

- **S1**：`dev.sh` 的就绪探测统一走 `wait_http` 重试（原先"已在运行"分支单次 curl 误报失败）。
- **S2**：门禁必须跑前端生产构建（`pnpm build`），CI 走同一个 gate.sh。
- **S3**：单测数量下限来自 `scripts/gate_baseline.json`，gate 低于下限即红。
- **S4**：门禁收尾恢复后必须跑 `check_demo_baseline`，且基线结论必须在最终 PASS/FAIL 行之前。
- **S5**：`check_gate_selftest` 只统计**真实存在**的检查器（`-f` 守卫的旧工具不算覆盖）。
- **S6**：CI 顶层最小权限、action 全部钉 commit SHA、MySQL 固定精确补丁版本、镜像构建+扫描 job。

`scripts/` 前缀是 `check_fake_assertions` 的已登记文本断言面（门禁脚本无 Python 运行时）；
CI 用 YAML 结构化读取，不做文本匹配。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "scripts/dev.sh"
GATE = ROOT / "scripts/gate.sh"
CI = ROOT / ".github/workflows/ci.yml"


def test_dev_sh_retries_health_checks():
    """S1：最终就绪探测必须是带重试的 wait_http，不是单次 curl。"""
    src = DEV.read_text(encoding="utf-8")
    assert "wait_http() {" in src, "dev.sh 缺少 wait_http 重试助手"
    assert 'wait_http "$BACKEND_URL" 20' in src, "后端就绪探测未走重试"
    assert 'wait_http "$FRONTEND_URL" 30' in src, "前端就绪探测未走重试"
    assert 'curl -sf --noproxy \'*\' -m 3 "$BACKEND_URL" > /dev/null && echo "后端 OK' not in src
    assert 'curl -sf --noproxy \'*\' -m 3 "$FRONTEND_URL" > /dev/null && echo "前端 OK' not in src


def test_unit_count_baseline_wired_into_gate():
    """S3：单测数量下限有独立基线文件，且 gate 按**收集数**比对（skip 也计入）。"""
    baseline = json.loads((ROOT / "scripts/gate_baseline.json").read_text(encoding="utf-8"))
    assert isinstance(baseline["min_passed"], int), "min_passed 必须是整数"
    assert baseline["min_passed"] >= 1271, "基线只许升不许降（降基线和删测同义）"

    gate = GATE.read_text(encoding="utf-8")
    assert "scripts/gate_baseline.json" in gate, "gate 未读取单测数量基线"
    assert "PASSED_COUNT" in gate and "SKIPPED_COUNT" in gate and "COLLECTED_COUNT" in gate
    assert 'if [ "$COLLECTED_COUNT" -lt "$MIN_PASSED" ]' in gate, (
        "gate 未按收集数比对（只数 passed 会在 CI 的时钟分支 skip 时误红）"
    )
    assert "单测数量基线 OK" in gate, "gate 未输出基线核对结果"


def test_dockerfile_pins_debian_release_for_apt_names():
    """S6 补充：基础镜像必须钉 Debian 版本（trixie 改名 libgdk-pixbuf 包，CI 已实证）。"""
    src = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    froms = [line.strip() for line in src.splitlines() if line.strip().startswith("FROM ")]
    assert "FROM python:3.13-slim-bookworm" in froms, (
        f"基础镜像未钉 bookworm（slim 会随 Debian 滚动换包名）：{froms}"
    )


def test_gate_builds_frontend_and_checks_baseline_after_restore():
    """S2 + S4：前端生产构建进 gate；恢复后基线结论必须在最终结论行之前。"""
    gate = GATE.read_text(encoding="utf-8")
    assert "pnpm build" in gate, "gate 未跑前端生产构建（dist 可与源码漂移）"
    assert "check_demo_baseline.py" in gate, "gate 恢复后未跑演示基线"
    assert gate.index("check_demo_baseline.py") < gate.index("===== 全量门禁 PASS"), (
        "基线核验发生在 PASS 结论之后——先报 PASS 再核基线等于没核"
    )


def test_gate_selftest_counts_only_real_checkers():
    """S5：gate 覆盖只数真实在岗的检查器（W8-D10：旧工具死分支已删除）。"""
    from scripts import check_gate_selftest as cg

    names = cg.gate_checker_names(GATE)
    assert "verify_architecture" in names
    assert "check_demo_baseline" in names
    assert "verify_api_contract" not in names, "盘上不存在的旧工具不是真实覆盖"
    assert "check_model_consistency" not in names, "盘上不存在的旧工具不是真实覆盖"
    # W8-D10（2026-10-28）：两个旧工具的 `if [ -f ... ] else skip` 死分支已从 gate 删除，
    # 能力由 [6] alembic check（模型一致性）+ [8] 契约快照两检（API 契约）覆盖。
    assert not cg.PENDING_TOOLS, "PENDING_TOOLS 应为空——死分支已删；再出现说明又加了不落地的工具"
    # 只看**代码行**：删除说明里会保留旧工具名（注释不是调用）
    gate_code = cg._code_lines(GATE.read_text(encoding="utf-8"))
    assert "verify_api_contract" not in gate_code and "check_model_consistency" not in gate_code


def test_ci_pins_actions_and_declares_permissions():
    """S6：最小权限 + action 钉 SHA + MySQL 精确版本 + 镜像构建/扫描 job。"""
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert data.get("permissions") == {"contents": "read"}, "CI 未声明最小权限"
    uses = [
        step["uses"]
        for job in data["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    assert uses, "CI 没有 action 步骤"
    floating = [spec for spec in uses if not re.search(r"@[0-9a-f]{40}$", spec)]
    assert floating == [], f"以下 action 未钉 commit SHA：{floating}"

    assert data["jobs"]["gate"]["services"]["mysql"]["image"] == "mysql:8.0.46", (
        "MySQL 未固定精确补丁版本（mysql:8.0 会随缓存刷新静默升级）"
    )
    assert data["jobs"]["image"]["continue-on-error"] is True, (
        "镜像尚未接入生产（待甲方裁定）——构建/扫描 job 不应拦门禁"
    )
    assert any("trivy" in spec for spec in uses), "CI 缺少镜像漏洞扫描"


# ---------------------------------------------------------------- B5-1（2026-11-27）


def test_gate_auto_writes_unit_count_baseline():
    """B5-1：gate PASS 时自动回写基线，且 CI 校验"基线 == 本次收集数"（严格相等）。

    修前实测：基线 1337，实际收集 1455——**118 个空档**，删 118 个测试照样绿。
    自动回写 + CI 严格相等，把这个空档压到 0；同时保持"只升不降"
    （自动下调会把删测洗成绿色）。
    """
    gate = GATE.read_text(encoding="utf-8")
    assert "scripts/gate_baseline_update.py" in gate, "gate 未自动回写单测数量基线（B5-1）"

    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = data["jobs"]["gate"]["steps"]
    drift = [s for s in steps if s.get("name") == "Gate baseline drift check (B5-1)"]
    assert drift, "CI 缺少基线漂移校验（B5-1 后半句：CI 校验两者相等）"
    run_src = drift[0]["run"]
    assert "git diff --quiet -- scripts/gate_baseline.json" in run_src
    assert "exit 1" in run_src, "基线漂移必须判红，不许只打印"


def test_baseline_update_only_raises_and_is_idempotent(tmp_path):
    """回写脚本的三条规则：只升不降 / 相等不落盘 / 高于则写入并留痕。"""
    from scripts.gate_baseline_update import update

    f = tmp_path / "gate_baseline.json"
    f.write_text(
        json.dumps({"min_passed": 100, "updated_at": "2026-01-01", "allowed_skips": {}}),
        encoding="utf-8",
    )

    before_bytes, before_mtime = f.read_bytes(), f.stat().st_mtime_ns
    assert update(100, path=f) is False, "相等也必须不落盘（否则 CI 每轮 diff 必红）"
    # 行为断言（比"内容相等"更强）：字节与 mtime 都没动 = 真的没写盘
    assert f.read_bytes() == before_bytes and f.stat().st_mtime_ns == before_mtime

    assert update(90, path=f) is False, "低于基线绝不下调（自动下调=把删测洗成绿）"
    assert json.loads(f.read_text(encoding="utf-8"))["min_passed"] == 100

    assert update(120, path=f, today="2026-11-27") is True
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["min_passed"] == 120
    assert data["last_collected"] == 120
    assert data["updated_at"] == "2026-11-27"
    assert data["allowed_skips"] == {}, "回写不得丢掉 allowed_skips 等既有键"

    # 二次同值调用（门禁每天跑）必须稳定：不再产生 diff
    snapshot_bytes, snapshot_mtime = f.read_bytes(), f.stat().st_mtime_ns
    assert update(120, path=f, today="2026-12-01") is False
    assert f.read_bytes() == snapshot_bytes and f.stat().st_mtime_ns == snapshot_mtime
