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
# 测试卫生两条：A 假绿断言（assert True/False 必须带注释）/ B 时间炸弹（测试里写死的近期未来绝对时间——
# 2026-09-20 实测 4 连红「开始时间必须在未来」，测试自己到期了；改相对时间或 ≥2050 远期哨兵）
run python -m scripts.check_fake_assertions
# E-20260912-01 防复发：小程序数据面断链（wxml 读的顶层变量必须真的进过 data）
run python scripts/check_miniapp_bindings.py
# E-20260912-05 防复发：文档引用悬空（文件路径/接口/配置键/表名/函数名必须真实存在）
run python scripts/check_docs_code_alignment.py
# E-20260912-09：RBAC 三方一致对账（宪法 §五.2 承诺项——声明/后端引用/前端引用）
run python scripts/check_rbac_consistency.py
# E-20260913：小程序风格基准（**R1–R14** 规则 + S1–S3 自证；含 R12 悬空类名 / R13 图标槽位 emoji/资产 /
# R14 WXML 注释与标签结构——2026-09-20 补：注释写成 */ 会吞掉半页模板且报错行指向别处，IDE 才看得见。
# 2026-09-20 修正：旧注释写"R1–R7"是陈旧的，规则实际已到 R13（见 docs/08 TD-11/TD-12）。不能替代目视截图）
run python scripts/check_miniapp_style.py
# fix44 R4（Q8 机化）：媒体纪律三条——M1 落盘单出口（只有 file_storage / Canvas.finish / save_jpeg
# 可写媒体文件）/ M2 破缓存单出口（`?v=` 只许出现在 file_utils.media_version）/ M3 清理脚本默认 dry-run。
# 含 S1 注入自证（--self-test 可单跑）。
run python scripts/check_media_discipline.py

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
  # fix44 R2（Q10 治根）：T27 原先只管后端快照，**不管前端类型产物** src/api/schema.d.ts
  # （docs/18 §五 曾写"漏 pnpm gen:api → gate[8] diff 红"，实际不红——职责空白已补）。
  # 契约源统一为快照（不再直连 8002），逐字节比对，无服务也能进 CI。
  if [ -f "admin-web/node_modules/.bin/openapi-typescript" ]; then
    run bash -c "cd admin-web && pnpm gen:api --check"
  else
    skip "admin-web 未安装依赖（pnpm install 后本步启用）"
  fi
else
  skip "契约快照未导出（docs/api/openapi.json 不存在）"
fi

step 9 "交付完整性检查（E-20260903-03 起源；G1 起扩为全仓 untracked 判红）"
# 仅本地有效（CI checkout 工作区天然干净）；工作区出现 untracked 文件 = 文件本体没入库，
# 硬失败（机械可判定，防呆原则）。**2026-09-23（G1）扩面**：原只扫 外部专家意见/docs/error_list，
# 于是漏掉了 scripts/gen_demo_progress_report.py——它是 seed_wm11_demo 的 **import 依赖**
# （别人 clone 后 dev.sh restart 会抛 ImportError），却因为不在那三个目录而一路绿灯。
# 现在改为：**任何 untracked 都判红**，白名单只有工具产物 .zcodeignore。
# quotepath 假阴性防呆：git 默认对中文路径输出八进制转义（E-20260903-03 同款坑，
# 专家三批复核时实测中招）——关闭 quotepath 转义后中文路径才可匹配
UNTRACKED_OTHERS=$(
  git -c core.quotepath=false status --porcelain | grep '^??' | grep -vE '^\?\? \.zcodeignore$' || true
)
if [ -n "$UNTRACKED_OTHERS" ]; then
  echo "✗ 工作区存在未入库文件（先 git add 或显式忽略；seed 依赖/文档引用的文件尤其不能留）："
  echo "$UNTRACKED_OTHERS"
  FAILED=1
else
  echo "交付完整性 PASS：工作区零 untracked（白名单 .zcodeignore）✓"
fi

sleep 0.3  # E-20260901-04：等 tee 落盘再退出，防日志末行截断
echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "===== 全量门禁 PASS（退出码 0） ====="
  # E-20260903-04 三犯史：①漏恢复 ②加提醒行 ③提醒行仍漏（2026-09-05）——
  # 证明"靠人记得"必败，裁量修正为全自动：检测到本地 dev 环境存活（8002 健康
  # 或 .dev-logs 存在）即自动 dev.sh restart（重启加载最新代码+双 seed 恢复）；
  # CI/无 dev 环境自动跳过，gate 纯验收语义保持。含后端改动批次由 restart 兜住
  # （E-20260905-01：start 幂等跳过=旧代码继续跑）
  if curl -sf -m 2 http://localhost:8002/health > /dev/null 2>&1 || [ -d ".dev-logs" ]; then
    echo "----- 检测到本地 dev 环境，自动恢复现场（dev.sh restart：重启后端+双 seed） -----"
    if bash scripts/dev.sh restart > /dev/null 2>&1; then
      echo "----- 现场已自动恢复 ✓ -----"
    else
      echo "⚠ 自动恢复失败——请手动执行: bash scripts/dev.sh restart"
    fi
  else
    echo "⚠ 业务表已被门禁清空——如需演示/目视请执行: bash scripts/dev.sh restart （重启加载最新代码+双 seed 恢复）"
  fi
  exit 0
else
  echo "===== 全量门禁 FAIL ====="
  exit 1
fi
