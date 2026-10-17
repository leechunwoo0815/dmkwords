#!/usr/bin/env bash
# ============================================================
# DmkWords 后端启动（**生产机**；docs/20 §一/§四）
#   用途: 以生产纪律启动 uvicorn —— 单进程、只绑 127.0.0.1（TLS 由 nginx 终结）
#   用法: bash deploy/start-backend.sh            # 前台运行（systemd / nohup / supervisor 包装）
#   判据: 启动前先跑配置硬校验（backend/config.py validate_production），不合规**直接拒启**
# 纪律（与 dev.sh 的差别，别混用）:
#   ① --host 127.0.0.1（dev.sh 的 0.0.0.0 仅开发态；生产禁 0.0.0.0）
#   ② 单进程、无 --workers（进程内 APScheduler + 命名锁护栏；副本数=1 是部署纪律）
#   ③ 禁 8443 镜像进程模式（历史 P1-F10：双进程同秒双跑定时任务）
# ============================================================

set -euo pipefail

cd "$(dirname "$0")/.."

# ── 时区钉死（P2-25，2026-10-17 第六十九批·大审查） ──
# 为什么必须钉：APScheduler 的时区写死 Asia/Shanghai，但**任务体**一律用宿主本地
# `datetime.now()` / `date.today()`。部署机若为 UTC（容器/云主机默认常见），03:30 的对账
# 会按 UTC 的"今天"算 → 落到**前天**账单；到期提醒/逾期标记同样整体偏 8 小时。
# 宿主时区本就正确时本行无副作用；显式传 TZ 之后，"部署机时区"不再是隐性前置条件。
export TZ="${TZ:-Asia/Shanghai}"
echo "[start-backend] TZ=$TZ（任务体用宿主本地时间，必须与 APScheduler 时区一致）"

PORT="${BACKEND_PORT:-8002}"
PYTHON_BIN=".venv/bin/python"
UVICORN_BIN=".venv/bin/uvicorn"

if [ ! -x "$UVICORN_BIN" ]; then
  echo "✗ 未找到 $UVICORN_BIN —— 先在部署机执行 uv sync 建好虚拟环境" >&2
  exit 1
fi

# ── 配置硬校验：生产判定看 APP_ENV（不是 DEBUG）；违规拒启 ──
echo "[start-backend] 配置校验中（APP_ENV=production）…"
if ! APP_ENV=production "$PYTHON_BIN" -c \
  'from backend.config import get_settings; get_settings().validate_production()'; then
  echo "✗ 生产配置校验未通过（问题见上一行）——修好 .env 再启动。常见修法：" >&2
  echo "   · 纯人工收款版本（未接商户号）：PAYMENT_ENABLED=false" >&2
  echo "   · 线上支付版本：补齐商户号四件套 + 两个 https 回调地址（docs/20 §五 / docs/21 §五）" >&2
  exit 1
fi
echo "[start-backend] 配置校验通过"

echo "[start-backend] uvicorn backend.main:app --host 127.0.0.1 --port $PORT（单进程）"
exec env APP_ENV=production "$UVICORN_BIN" backend.main:app \
  --host 127.0.0.1 --port "$PORT"
