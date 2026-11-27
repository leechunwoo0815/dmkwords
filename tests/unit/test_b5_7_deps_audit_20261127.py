# tests/unit/test_b5_7_deps_audit_20261127.py — 依赖 CVE 审计的机械判据（B5-7）
"""审查① T6：依赖面没有任何持续信号——`uv.lock` 一锁就是几个月，出了 CVE 没人知道。

本批落地三层（互补，缺一层就有盲区）：
  ① `scripts/audit_deps.sh`：`uv export` 导出**锁定版本** → `pip-audit -r`（审计对象是
     "任何机器按 lock 装出来的东西"，不是本机 venv 里碰巧装了什么）；
  ② `.github/workflows/deps-audit.yml`：**周跑** + 依赖面变更即跑 + 可手动触发，报告留档 30 天；
  ③ `.github/dependabot.yml`：uv / npm(admin-web) / github-actions 三生态周更。

**fail-closed**：审计跑不起来（网络/OSV 不可达）必须 `exit 2`——"查不了"不许当成"没问题"
（与 B2-2 `log_alert.sh` 同一条纪律）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts/audit_deps.sh"
WORKFLOW = ROOT / ".github/workflows/deps-audit.yml"
DEPENDABOT = ROOT / ".github/dependabot.yml"


def test_audit_script_uses_locked_versions_not_venv():
    """审计对象必须是 uv.lock 导出的固定版本（venv 装了什么取决于上次 sync 的时点）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "uv export --format requirements-txt" in src, "未导出锁定版本"
    assert "--no-emit-project" in src, "本仓库自身是可编辑安装，不应参与审计"
    assert "pip-audit" in src, "未调用 pip-audit"
    assert "--strict" in src, "pip-audit 未开 strict（工具/依赖告警会被静默放过）"


def test_audit_script_is_fail_closed():
    """网络/工具不可用 → exit 2（不许把"查不了"显示成"没问题"）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert src.count("exit 2") >= 2, "uv 不可用 / uv export 失败都必须 fail-closed"
    assert "fail-closed" in src
    # bash 3.2（macOS 自带）会把全角括号并进变量名：`$VAR）` → unbound variable。
    # 2026-11-27 实测踩到（脚本第一次跑就报 `REPORT<乱码>: unbound variable`）。
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", src), (
        "存在 `$VAR` 紧跟非 ASCII 字符的写法——bash 3.2 会把它并进变量名，改用 ${VAR}"
    )


def test_weekly_workflow_and_report_artifact():
    """周跑 + 依赖面变更即跑 + 报告留档；action 仍钉 SHA、权限仍最小。"""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 会把裸 `on:` 解析成布尔 True，故两种键都认
    triggers = data.get("on", data.get(True))
    assert triggers, "缺少触发器"
    cron = triggers["schedule"][0]["cron"]
    assert cron == "0 3 * * 1", f"必须是每周一次（实际 {cron}）——按天跑只会制造噪音"
    assert "workflow_dispatch" in triggers, "缺手动触发（发版前要能立刻查一次）"
    paths = triggers["push"]["paths"]
    assert "uv.lock" in paths and "pyproject.toml" in paths, "依赖面变了必须立刻审"

    job = data["jobs"]["audit"]
    assert data["permissions"] == {"contents": "read"}, "必须声明最小权限"
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    floating = [spec for spec in uses if not re.search(r"@[0-9a-f]{40}$", spec)]
    assert floating == [], f"action 未钉 commit SHA：{floating}"

    upload = [s for s in job["steps"] if "upload-artifact" in s.get("uses", "")]
    assert upload and upload[0].get("if") == "always()", "审计报告必须无条件留档（红的那次最需要）"


def test_dependabot_covers_three_ecosystems():
    """uv / npm / github-actions 各一条——npm 侧本机 pnpm 走镜像没有 audit 端点，只能靠它。"""
    data = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert data["version"] == 2
    ecosystems = {u["package-ecosystem"] for u in data["updates"]}
    assert ecosystems == {"uv", "npm", "github-actions"}, f"覆盖面不全：{ecosystems}"
    for u in data["updates"]:
        assert u["schedule"]["interval"] == "weekly", f"{u['package-ecosystem']} 未按周跑"
        assert u["open-pull-requests-limit"] <= 5, "PR 上限过高会淹掉正常评审"
