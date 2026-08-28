#!/usr/bin/env bash
# scripts/gate.sh — 全量质量门禁（宪法第八节）
# 用法：bash scripts/gate.sh full   退出码 0 才算完成
set -uo pipefail
cd "$(dirname "$0")/.."

# venv 注入（uv 标准 .venv）
if [ -d ".venv/bin" ]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi

FAILED=0
step() {
  echo ""
  echo "===== [$1] $2 ====="
}

run() {
  if ! "$@"; then
    echo "✗ 门禁失败: $*"
    FAILED=1
  fi
}

skip() {
  echo "（跳过：$1）"
}

step 1 "lint (ruff)"
run ruff check backend/ tests/ features/ scripts/
run ruff format --check .

step 2 "单测（真实 MySQL 同构环境）"
run python -m pytest tests/ -x -q --tb=short

step 3 "BDD (behave)"
run python -m behave features/ --no-capture -q

step 4 "架构关"
run python -m scripts.verify_architecture

step 5 "契约与反假绿"
# verify_api_contract / check_model_consistency 为旧项目工具，F0 按新代码结构重写后启用
if [ -f "scripts/verify_api_contract.py" ] && grep -q "dmkwords" scripts/verify_api_contract.py 2>/dev/null; then
  run python -m scripts.verify_api_contract
else
  skip "verify_api_contract 旧结构工具待 F0 重写"
fi
if [ -f "scripts/check_model_consistency.py" ] && grep -q "dmkwords" scripts/check_model_consistency.py 2>/dev/null; then
  run python -m scripts.check_model_consistency
else
  skip "check_model_consistency 旧结构工具待 F0 重写"
fi
run python -m scripts.check_fake_assertions

step 6 "数据库迁移一致性"
# 首个迁移文件创建后启用：
if ls alembic/versions/*.py >/dev/null 2>&1; then
  run alembic upgrade head
  run alembic check
else
  skip "alembic versions 为空，F0 起启用"
fi

step 7 "覆盖率（WM1 基线 25%，随模块逐级抬升，终态整体85/关键域90）"
# 宪法目标 85/90，当前实测 ~78.7%，抬线决策见 docs/09 偿债窗口条目（验收期不抬）
run python -m pytest tests/ -q --cov=backend --cov-fail-under=25 --cov-report=term-missing:skip-covered

step 8 "前端类型检查（tsc）"
if [ -f "admin-web/node_modules/.bin/tsc" ]; then
  run bash -c "cd admin-web && pnpm exec tsc --noEmit"
else
  skip "admin-web 未安装依赖（pnpm install 后启用）"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "===== 全量门禁 PASS（退出码 0） ====="
  exit 0
else
  echo "===== 全量门禁 FAIL ====="
  exit 1
fi
