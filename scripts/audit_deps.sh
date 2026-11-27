#!/usr/bin/env bash
# scripts/audit_deps.sh — 依赖 CVE 审计（B5-7，2026-11-27）
# 用法：bash scripts/audit_deps.sh [--json 报告路径]
#
# 为什么审计 **uv.lock 导出的固定版本**而不是当前 venv：
#   · venv 里装了什么取决于上次 `uv sync` 的时点与可选 extra（sms-*），审计范围不确定；
#   · `uv export` 出来的才是"任何一台机器按 lock 装出来的东西"，CI 与生产同一份；
#   · `--no-emit-project` 排除本仓库自身（可编辑安装的本地包不需要审计）。
#
# 为什么单独一个工作流而不是塞进门禁：pip-audit 要访问 OSV/PyPI，网络抖动会把全量门禁拖红。
# 门禁管"代码正确性"，本脚本管"依赖供应链"，节奏是**周跑 + 手动**（见 .github/workflows/deps-audit.yml）。
#
# 退出码：0 = 无已知漏洞；1 = 有漏洞（报告已落盘）；2 = 工具/网络问题（**也是失败**，fail-closed：
# "查不了"不许当成"没问题"——B2-2 同款纪律）。
set -uo pipefail
cd "$(dirname "$0")/.."

REPORT=".dev-logs/deps-audit.json"
if [ "${1:-}" = "--json" ]; then REPORT="${2:-.dev-logs/deps-audit.json}"; fi
mkdir -p "$(dirname "$REPORT")"

TMP_REQ=$(mktemp -t dmk-requirements-XXXXXX.txt)
trap 'rm -f "$TMP_REQ"' EXIT

echo "===== 依赖 CVE 审计（B5-7）====="
if ! command -v uv > /dev/null 2>&1; then
  echo "✗ uv 不可用——无法导出锁定版本（fail-closed，不当作通过）"
  exit 2
fi

if ! uv export --format requirements-txt --no-emit-project --no-hashes > "$TMP_REQ" 2> /dev/null; then
  echo "✗ uv export 失败——读不到锁定版本（fail-closed）"
  exit 2
fi
echo "锁定依赖导出 OK：$(grep -c '==' "$TMP_REQ") 个固定版本"

# pip-audit 不进项目依赖（它只是审计工具；装进 lock 会给生产镜像增面）
if ! uvx --from pip-audit pip-audit \
  -r "$TMP_REQ" \
  --progress-spinner off \
  --strict \
  --format json \
  --output "$REPORT"; then
  RC=$?
  if [ "$RC" -eq 1 ]; then
    echo "✗ 发现已知漏洞——详情：${REPORT}"
    echo "  处置：能升就升（uv lock --upgrade-package <pkg>），不能升的登记进 docs/09 并写清缓解措施"
  else
    echo "✗ pip-audit 执行失败（退出 ${RC}）——网络/OSV 不可达也算失败（fail-closed）"
  fi
  exit "$RC"
fi

echo "✓ 依赖 CVE 审计通过：未发现已知漏洞（报告：${REPORT}）"
