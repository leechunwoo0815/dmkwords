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

# 2026-10-12 审查 P2：缺件类问题必须判红（原先是静默 skip，删个目录就能让红线无声消失）
fail() {
  echo "✗ 门禁失败: $1"
  FAILED=1
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

step 4 "架构关（含自证）"
# 第八轮审查 P1-10：先跑 `--self-test`（注入违例必须报得出来）再跑主流程——
# 原先只跑主流程，实测把 sqlite 禁令关掉后照样打印 `架构关 PASS`。
run python -m scripts.verify_architecture --self-test
run python -m scripts.verify_architecture

step 5 "契约与反假绿（**含检查器自证**）"
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
# ---------------------------------------------------------------------------
# 检查器清单（**唯一来源**）：每个检查器都先跑 `--self-test` 再跑主流程。
# 第八轮审查 P1-10：原先只跑主流程，注释里写着"含 S1 注入自检"而**实际没传 --self-test**——
# 实测把 `?v=` 改坏 / 关掉 sqlite 禁令 / 让 R14 永远返回 [] / 把文档"文件存在性"改成
# `if False and ...`，gate 全部照样 PASS（"改了检查器没人知道"是本项目最贵的失败模式）。
# 本清单由 `scripts/check_gate_selftest.py` 机械校验：仓库里的检查器**要么在清单里、
# 要么在它的 NOT_IN_GATE 白名单里写明理由**——新增检查器忘了接进 gate 同样判红。
# ---------------------------------------------------------------------------
# 测试卫生两条：A 假绿断言（assert True/False 必须带注释）/ B 时间炸弹（测试里写死的近期未来绝对时间——
# 2026-09-20 实测 4 连红「开始时间必须在未来」，测试自己到期了；改相对时间或 ≥2050 远期哨兵）
# E-20260912-01 防复发：小程序数据面断链（wxml 读的顶层变量必须真的进过 data）
# A1-11（2026-10-09）：小程序**接线**两条——W1 `bind*/catch*` 指向的 handler 必须存在
# （P0-2 退款死按钮的漏网形状：wxml 有 bindtap、js 无该方法 → 点了毫无反应）；
# W2 插值类名必须能解析到样式（`banner-{{index % 3}}` 三变体从未定义 → 底色静默失效）。
# E-20260912-05 防复发：文档引用悬空（文件路径/接口/配置键/表名/函数名必须真实存在）
# E-20260912-09：RBAC 三方一致对账（宪法 §五.2 承诺项——声明/后端引用/前端引用）
# P1 #10（2026-10-15 大审查）：仪表盘待办卡的行**必须覆盖后端 admin_total 全部求和项**——
# 两侧各改各的就会假绿：卡片绿色「全部处理完毕」而同屏侧边栏徽标 >0（被掩盖的常是资金异常）。
# E-20260913：小程序风格基准（**R1–R16** 规则 + S1–S3 自证；含 R12 悬空类名 / R13 图标槽位 emoji/资产 /
# R14 WXML 注释与标签结构——注释写成 */ 会吞掉半页模板且报错行指向别处，IDE 才看得见。不能替代目视截图）
# fix44 R4（Q8 机化）：媒体纪律三条——M1 落盘单出口（只有 file_storage / Canvas.finish / save_jpeg
# 可写媒体文件）/ M2 破缓存单出口（`?v=` 只许出现在 file_utils.media_version）/ M3 清理脚本默认 dry-run。
# G6（docs/09）+ docs/15 §21.3：**文案 emoji 口径扩到管理端与后端导出**（R13d）——
# ① admin-web/src/**/*.{ts,tsx} 去注释后零 emoji；② backend/**/*.py 字符串字面量零 emoji
# （AST 判定、docstring 跳过）。排版字形（✓ ✕ ★ ☆ 等）白名单放行。
# 第八轮审查 P2-69（2026-10-21）：**BDD 挂起基线守卫**——behave.ini 的 `~@draft` 让一行标签
# 就能把场景摘出门禁；本检查器把"哪些文件/场景被挂起 + 为什么"钉成基线（features/bdd_baseline.json），
# 挂起集合或活动面（features/scenarios/steps）与基线不一致即判红。
CHECKER_CMDS=(
  "python -m scripts.check_fake_assertions"
  "python -m scripts.check_bdd_baseline"
  "python scripts/check_miniapp_bindings.py"
  "python scripts/check_miniapp_wiring.py"
  "python -m scripts.check_docs_code_alignment"
  "python -m scripts.check_rbac_consistency"
  "python scripts/check_admin_todo_rows.py"
  "python scripts/check_miniapp_style.py"
  "python scripts/check_media_discipline.py"
  "python scripts/check_copy_emoji.py"
  "python -m scripts.check_gate_selftest"
)
for checker_cmd in "${CHECKER_CMDS[@]}"; do
  run $checker_cmd --self-test
  run $checker_cmd
done

step 6 "数据库迁移一致性"
if ls alembic/versions/*.py >/dev/null 2>&1; then
  run alembic upgrade head
  run alembic check
else
  # 第八轮审查 P1-10 ④：**空目录判红**。原先是静默 skip——删掉迁移文件就能让
  # "迁移一致性"这条红线无声消失（本项目已有 37 个迁移，空目录只可能是事故）。
  fail "alembic/versions 为空——迁移目录缺失或被清空（不许静默跳过）"
fi

step 7 "前端类型检查（tsc）"
if [ -f "admin-web/node_modules/.bin/tsc" ]; then
  run bash -c "cd admin-web && pnpm exec tsc --noEmit"
elif [ -n "${CI:-}" ]; then
  fail "CI 环境缺 admin-web 依赖（node_modules/.bin/tsc 不存在）——pnpm install 后重跑"
else
  # 2026-10-12 审查 P2：**本地缺件也判红**。原先是静默 skip——删掉 node_modules 或契约快照
  # 就能让"tsc 0 错误 / API 类型禁手写"两条红线无声消失（而 CI 装着依赖照样绿，正好掩盖本地这次跳过）。
  # 逃生口是显式的：`SKIP_FRONTEND_CHECKS=1`（须在门禁日志里留痕）。
  if [ "${SKIP_FRONTEND_CHECKS:-0}" = "1" ]; then
    skip "显式 SKIP_FRONTEND_CHECKS=1（本地临时跳过 tsc，不得用于交付）"
  else
    fail "admin-web 未安装依赖（node_modules/.bin/tsc 不存在）——请 pnpm install；如确需临时跳过：SKIP_FRONTEND_CHECKS=1"
  fi
fi

step 8 "契约快照检查（T27：破坏性变更必须改代码+更新快照两步显形）"
if [ -f "docs/api/openapi.json" ]; then
  run python scripts/export_openapi.py --check
  # fix44 R2（Q10 治根）：T27 原先只管后端快照，**不管前端类型产物** src/api/schema.d.ts
  # （docs/18 §五 曾写"漏 pnpm gen:api → gate[8] diff 红"，实际不红——职责空白已补）。
  # 契约源统一为快照（不再直连 8002），逐字节比对，无服务也能进 CI。
  if [ -f "admin-web/node_modules/.bin/openapi-typescript" ]; then
    run bash -c "cd admin-web && pnpm gen:api --check"
  elif [ -n "${CI:-}" ]; then
    # P3 门禁族（2026-10-22 第一百一十四批）：[7] 步有 CI 硬失败分支、[8] 步原先**没有**——
    # CI 里缺依赖会走逃生口静默 skip（前端类型产物比对凭空消失，与 [7] 步同一红线）。
    fail "CI 环境缺 admin-web 依赖（node_modules/.bin/openapi-typescript 不存在）——pnpm install 后重跑"
  elif [ "${SKIP_FRONTEND_CHECKS:-0}" = "1" ]; then
    skip "显式 SKIP_FRONTEND_CHECKS=1（本地临时跳过前端类型产物比对）"
  else
    fail "admin-web 未安装依赖（node_modules/.bin/openapi-typescript 不存在）——请 pnpm install"
  fi
else
  fail "契约快照未导出（docs/api/openapi.json 不存在）——请 python -m scripts.export_openapi"
fi

step 9 "交付完整性检查（E-20260903-03 起源；G1 起扩为全仓 untracked 判红）"
# 仅本地有效（CI checkout 工作区天然干净）；工作区出现 untracked 文件 = 文件本体没入库，
# 硬失败（机械可判定，防呆原则）。**2026-09-23（G1）扩面**：原只扫 外部专家意见/docs/error_list，
# 于是漏掉了 scripts/gen_demo_progress_report.py——它是 seed_wm11_demo 的 **import 依赖**
# （别人 clone 后 dev.sh restart 会抛 ImportError），却因为不在那三个目录而一路绿灯。
# 现在改为：**任何 untracked 都判红**，白名单只有工具产物 .zcodeignore。
# quotepath 假阴性防呆：git 默认对中文路径输出八进制转义（E-20260903-03 同款坑，
# 专家三批复核时实测中招）——关闭 quotepath 转义后中文路径才可匹配
# P3 门禁族（2026-10-22 第一百一十四批）：**git 不可用必须判红**——
# 原先 `git status | grep ... || true` 把"git 报错"和"没有 untracked"压成同一种结果
# （都得到空串 → 照印 PASS），交付完整性会在 git 坏掉时静默失效。
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  echo "✗ 交付完整性无法执行：当前不是 git 工作树（或 git 不可用）——不许静默 PASS"
  FAILED=1
else
  GIT_STATUS_OUT=$(git -c core.quotepath=false status --porcelain 2>&1) || {
    echo "✗ git status 执行失败（交付完整性检查无法完成）："
    echo "$GIT_STATUS_OUT"
    FAILED=1
    GIT_STATUS_OUT=""
  }
  UNTRACKED_OTHERS=$(printf '%s\n' "$GIT_STATUS_OUT" | grep '^??' | grep -vE '^\?\? \.zcodeignore$' || true)
  if [ -n "$UNTRACKED_OTHERS" ]; then
    echo "✗ 工作区存在未入库文件（先 git add 或显式忽略；seed 依赖/文档引用的文件尤其不能留）："
    echo "$UNTRACKED_OTHERS"
    FAILED=1
  elif [ "$FAILED" -eq 0 ]; then
    echo "交付完整性 PASS：工作区零 untracked（白名单 .zcodeignore）✓"
  fi
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
    if bash scripts/dev.sh restart >> "$GATE_LOG_FILE" 2>&1; then
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
  # 2026-10-12 审查 P2：**失败分支同样恢复现场**。pytest 步骤已 TRUNCATE 全部业务表，
  # 而"门禁红"恰是最容易忘记恢复的时刻（此前只有 PASS 分支自动 restart，
  # docs/19 记录过三次"库里空了没人发现"的事故）。恢复失败不影响退出码语义。
  if curl -sf -m 2 http://localhost:8002/health > /dev/null 2>&1 || [ -d ".dev-logs" ]; then
    echo "----- 门禁失败但仍是本地 dev 环境：自动恢复现场（dev.sh restart） -----"
    if bash scripts/dev.sh restart >> "$GATE_LOG_FILE" 2>&1; then
      echo "----- 现场已自动恢复 ✓（业务表已回填演示数据） -----"
    else
      echo "⚠ 自动恢复失败——请手动执行: bash scripts/dev.sh restart"
    fi
  else
    echo "⚠ 业务表已被门禁清空——如需演示/目视请执行: bash scripts/dev.sh restart"
  fi
  exit 1
fi
