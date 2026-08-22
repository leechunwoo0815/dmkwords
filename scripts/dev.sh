#!/usr/bin/env bash
# scripts/dev.sh — DmkWords 开发环境一键启动
# 用法：
#   bash scripts/dev.sh          # 启动全部（MySQL + 后端 + 管理端前端）
#   bash scripts/dev.sh stop     # 停止
#   bash scripts/dev.sh status   # 查看状态
set -uo pipefail
cd "$(dirname "$0")/.."

LOG_DIR=".dev-logs"
mkdir -p "$LOG_DIR"

BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
BACKEND_URL="http://localhost:8002/health"
FRONTEND_URL="http://localhost:5173"

is_running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

wait_mysql() {
  echo "等待 MySQL 健康..."
  for _ in $(seq 1 30); do
    hstat=$(docker inspect --format='{{.State.Health.Status}}' dmkwords-mysql 2>/dev/null || true)
    [ "$hstat" = "healthy" ] && echo "MySQL healthy" && return 0
    sleep 2
  done
  echo "✗ MySQL 30 次探测未健康，请查看: docker logs dmkwords-mysql"
  return 1
}

start() {
  echo "===== [1/4] MySQL（OrbStack 容器） ====="
  docker compose up -d mysql
  wait_mysql || exit 1

  echo "===== [2/4] 数据库迁移 + 种子（幂等） ====="
  .venv/bin/alembic upgrade head || exit 1
  .venv/bin/python -m backend.seeds.seed_admin
  .venv/bin/python -m backend.seeds.seed_configs | head -1

  echo "===== [3/4] 后端 API（:8002） ====="
  if is_running "$BACKEND_PID_FILE"; then
    echo "⚠ 后端已在运行（pid $(cat "$BACKEND_PID_FILE")）——运行的是启动时的代码，不会自动加载新提交"
    echo "  拉取/修改过代码后请执行: bash scripts/dev.sh restart"
  else
    nohup .venv/bin/uvicorn backend.main:app --port 8002 > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
    echo $! > "$BACKEND_PID_FILE"
    for _ in $(seq 1 20); do
      curl -sf -m 3 "$BACKEND_URL" > /dev/null && break
      sleep 1
    done
  fi
  curl -sf -m 3 "$BACKEND_URL" > /dev/null && echo "后端 OK: $BACKEND_URL" || { echo "✗ 后端未就绪，查看 $LOG_DIR/backend.log"; exit 1; }

  echo "===== [4/4] 管理端前端（:5173） ====="
  if is_running "$FRONTEND_PID_FILE"; then
    echo "前端已在运行（pid $(cat "$FRONTEND_PID_FILE")）"
  else
    (cd admin-web && [ -d node_modules ] || pnpm install --silent)
    nohup pnpm --dir admin-web dev > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
    echo $! > "$FRONTEND_PID_FILE"
    for _ in $(seq 1 30); do
      curl -sf -m 3 "$FRONTEND_URL" > /dev/null && break
      sleep 1
    done
  fi
  curl -sf -m 3 "$FRONTEND_URL" > /dev/null && echo "前端 OK: $FRONTEND_URL" || { echo "✗ 前端未就绪，查看 $LOG_DIR/frontend.log"; exit 1; }

  echo ""
  echo "============================================"
  echo "  DmkWords 开发环境就绪"
  echo "  管理后台:   http://localhost:5173"
  echo "  后端 API:   http://localhost:8002/health"
  echo "  测试账号:   admin / dmkwords123（超管）"
  echo "              staff01 / dmkwords123（运营专员）"
  echo "  日志:       $LOG_DIR/  |  停止: bash scripts/dev.sh stop"
  echo "============================================"
}

stop() {
  for pid_file in "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"; do
    if is_running "$pid_file"; then
      pid=$(cat "$pid_file")
      # 杀进程组（vite 会 spawn 子进程）
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      echo "已停止 pid $pid（$pid_file）"
    fi
    rm -f "$pid_file"
  done
  echo "（MySQL 容器保持运行；如需停止: docker compose stop mysql）"
}

status() {
  echo "MySQL:  $(docker inspect --format='{{.State.Health.Status}}' dmkwords-mysql 2>/dev/null || echo 未运行)"
  if is_running "$BACKEND_PID_FILE"; then echo "后端:   运行中 (pid $(cat "$BACKEND_PID_FILE"))"; else echo "后端:   未运行"; fi
  if is_running "$FRONTEND_PID_FILE"; then echo "前端:   运行中 (pid $(cat "$FRONTEND_PID_FILE"))"; else echo "前端:   未运行"; fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "用法: bash scripts/dev.sh [start|stop|restart|status]"; exit 1 ;;
esac
