#!/usr/bin/env bash
# LOOP-2 机械门禁运行器 —— 通过/失败的唯一裁决者（退出码即裁决，模型不得自行解读输出文本）
#
# 用法:
#   bash scripts/loop_gate.sh quick            # 单项快门禁: 对改动文件跑 ruff check + format --check
#   bash scripts/loop_gate.sh quick -k expr    # 附加 pytest -k expr 针对性测试
#   bash scripts/loop_gate.sh full             # 全量十一关 + MySQL 并发实证（约 12 分钟，勿中断）
#
# 退出码: 0=PASS  1=FAIL  2=ABORT（环境缺失，如 MySQL 未启动）
# 日志: audit_loop/loop2/gate-runs/<时间戳>-<级别>.log（台账 evidence 列必须引用真实路径）
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

TS=$(date +%Y%m%d-%H%M%S)
RUNDIR=audit_loop/loop2/gate-runs
mkdir -p "$RUNDIR"
LEVEL="${1:-quick}"
shift 2>/dev/null || true
LOG="$RUNDIR/$TS-$LEVEL.log"
ITEMS=0
FAIL=0

echo "[loop_gate] level=$LEVEL start=$(date '+%F %T') cwd=$(pwd)" >"$LOG"

run_item() { # run_item <段名> <命令...>
  local name="$1"; shift
  ITEMS=$((ITEMS + 1))
  echo "===== $name =====" | tee -a "$LOG"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "[GATE-ITEM] $name FAIL rc=$rc" | tee -a "$LOG"
    FAIL=1
  else
    echo "[GATE-ITEM] $name OK" | tee -a "$LOG"
  fi
}

mysql_ready() {
  venv/bin/python -c "import pymysql; pymysql.connect(host='localhost', user='root', database='dmkwords', connect_timeout=3)" >/dev/null 2>&1
}

case "$LEVEL" in
quick)
  # 收集改动 py 文件（未提交 + 最近一次提交），去重
  CHANGED=$( { git status --porcelain 2>/dev/null | awk '{print $NF}'; git diff --name-only HEAD~1 HEAD 2>/dev/null; } | sort -u | grep '\.py$' || true)
  if [ -n "$CHANGED" ]; then
    # shellcheck disable=SC2086
    run_item "RUFF-CHECK(改动文件)" venv/bin/ruff check $CHANGED
    # shellcheck disable=SC2086
    run_item "RUFF-FORMAT(改动文件)" venv/bin/ruff format --check $CHANGED
  else
    echo "[GATE-ITEM] 无改动 py 文件，跳过 ruff" | tee -a "$LOG"
  fi
  # 可选: -k 表达式 → 针对性 pytest
  if [ "${1:-}" = "-k" ]; then
    shift
    run_item "PYTEST(-k $*)" venv/bin/python -m pytest tests/ -q --tb=short -p no:cacheprovider -k "$*"
  fi
  ;;
full)
  if ! mysql_ready; then
    echo "[GATE:full] ABORT 本机 MySQL(root@localhost:3306/dmkwords) 不可用——禁止跳过 MySQL 关，请先启动 MySQL 再重跑" | tee -a "$LOG"
    exit 2
  fi
  # ── 与 专家意见/项目交接-20260809.md §七 门禁命令逐字一致（全量，不可缩范围）──
  run_item "RUFF-CHECK-1" venv/bin/ruff check backend/ tests/
  run_item "RUFF-CHECK-2" venv/bin/ruff check features/ scripts/
  run_item "RUFF-FORMAT" venv/bin/ruff format --check .
  run_item "PYTEST" venv/bin/python -m pytest tests/ -q --tb=short -p no:cacheprovider
  run_item "BEHAVE" venv/bin/python -m behave features/ --no-capture -q
  run_item "CONTRACT" venv/bin/python -m scripts.verify_api_contract
  run_item "MODEL" venv/bin/python -m scripts.check_model_consistency
  run_item "WIRING" venv/bin/python -m scripts.verify_action_wiring --strict
  run_item "CONFIG-DOC" env PYTHONPATH=. venv/bin/python scripts/gen_config_doc.py --check
  run_item "INTEGRATION" env MOCK_PAYMENT=true MOCK_SMS=true DEBUG=true venv/bin/python scripts/integration_test.py
  run_item "ALEMBIC-CHECK" venv/bin/python -m alembic check
  run_item "MYSQL-CONCURRENCY" venv/bin/python scripts/verify_mysql_concurrency.py
  ;;
*)
  echo "用法: loop_gate.sh quick [-k expr] | full" >&2
  exit 2
  ;;
esac

if [ "$FAIL" -eq 0 ]; then
  echo "[GATE:$LEVEL] PASS items=$ITEMS end=$(date '+%F %T')" | tee -a "$LOG"
  exit 0
else
  echo "[GATE:$LEVEL] FAIL items=$ITEMS end=$(date '+%F %T')" | tee -a "$LOG"
  exit 1
fi
