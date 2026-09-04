#!/usr/bin/env bash
# scripts/gate.sh — 全量质量门禁（宪法第八节）
# 用法：bash scripts/gate.sh full   退出码 0 才算完成
set -uo pipefail
cd "$(dirname "$0")/.."

# venv 注入（uv 标准 .venv）
if [ -d ".venv/bin" ]; then
  export PATH="$PWD/.venv/bin:$PATH"
fi

# --- v13 mkdir 教训终结（E-20260901-04）：日志自动归档，外部 mkdir/tee 从此不存在 ---
# 用法不变：bash scripts/gate.sh full；可选 GATE_LOG_NAME=批次名 自定义日志文件名
# （开发模型多次忘建目录 tee 白跑，提示词约束无效——流程性纪律一律进脚本，防呆设计）
GATE_LOG_DIR="gate-runs/$(date +%Y-%m-%d)"
GATE_LOG_FILE="${GATE_LOG_DIR}/gate-${GATE_LOG_NAME:-$(date +%H%M%S)}.log"
mkdir -p "$GATE_LOG_DIR"
exec > >(tee -a "$GATE_LOG_FILE") 2>&1
echo "[gate] 本轮日志自动归档: ${GATE_LOG_FILE}"

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

step 2 "单测 + 覆盖率（真实 MySQL 同构环境，单次跑全量）"
# 2026-09-02 优化裁定（用户）：原 [2] 纯单测 + [7] 带覆盖率跑同一批测试两遍，
# 占门禁总时长 96%（550s+712s）且双轮内存高峰——合并为单次（-42% 总时长）；
# cov-fail-under 照常拦截；-x 快速失败语义随合并取消（门禁本就要看全貌）。
# --durations=20：输出 top 慢测试清单（纯观测，为后续并行化铺路）
run python -m pytest tests/ -q --tb=short --cov=backend --cov-fail-under=25 --cov-report=term-missing:skip-covered --durations=20

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

step 7 "前端类型检查（tsc）"
if [ -f "admin-web/node_modules/.bin/tsc" ]; then
  run bash -c "cd admin-web && pnpm exec tsc --noEmit"
else
  skip "admin-web 未安装依赖（pnpm install 后启用）"
fi

step 8 "契约快照检查（T27：破坏性变更必须改代码+更新快照两步显形）"
if [ -f "docs/api/openapi.json" ]; then
  run python scripts/export_openapi.py --check
else
  skip "契约快照未导出（docs/api/openapi.json 不存在）"
fi

step 9 "交付完整性检查（E-20260903-03：引用文件必须入库——missing files 惯犯第 3 次防呆）"
# 仅本地有效（CI checkout 工作区天然干净）；外部专家意见/docs/error_list 下出现
# untracked 文件 = LEDGER/文档引用与文件本体分离，硬失败（机械可判定，防呆原则）
UNTRACKED_DOCS=$(git status --porcelain | grep '^??' | grep -E '外部专家意见/|docs/|error_list/' || true)
if [ -n "$UNTRACKED_DOCS" ]; then
  echo "✗ 应入库目录下存在 untracked 文件（LEDGER/文档引用的文件必须同刀或后刀入库）："
  echo "$UNTRACKED_DOCS"
  FAILED=1
else
  echo "交付完整性 PASS：引用目录零 untracked ✓"
fi

sleep 0.3  # E-20260901-04：等 tee 落盘再退出，防日志末行截断
echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "===== 全量门禁 PASS（退出码 0） ====="
  exit 0
else
  echo "===== 全量门禁 FAIL ====="
  exit 1
fi
