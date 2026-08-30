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
  # 端口防呆：8002 被"非 pid 文件管理"的进程占用时，新进程会绑定失败退出，
  # 而健康检查会打到旧进程上假装 OK（假绿）——必须先暴露出来
  if [ ! -s "$BACKEND_PID_FILE" ] || ! is_running "$BACKEND_PID_FILE"; then
    port_pid=$(lsof -ti :8002 2>/dev/null | head -1)
    if [ -n "$port_pid" ]; then
      echo "✗ 端口 8002 被进程 ${port_pid} 占用（不是 dev.sh 管理的后端，代码可能是旧的）"
      echo "  这会导致新代码不生效、接口 404。处理方式："
      echo "    kill ${port_pid} && bash scripts/dev.sh start"
      exit 1
    fi
  fi
  if is_running "$BACKEND_PID_FILE"; then
    echo "⚠ 后端已在运行（pid $(cat "$BACKEND_PID_FILE")）——运行的是启动时的代码，不会自动加载新提交"
    echo "  拉取/修改过代码后请执行: bash scripts/dev.sh restart"
  else
    nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8002 > "$LOG_DIR/backend.log" 2>&1 < /dev/null &
    echo $! > "$BACKEND_PID_FILE"
    for _ in $(seq 1 20); do
      # --noproxy：本机探测绕过系统代理（http_proxy 会把 localhost 探测打到 7890 代理上，返回 502 假崩）
  curl -sf --noproxy '*' -m 3 "$BACKEND_URL" > /dev/null && break
      sleep 1
    done
    # 真机 iOS 媒体（image/audio）拦 http——提供本地 https 端口（certs/ 存在时起 8443）
    if [ -f certs/server.crt ] && [ -f certs/server.key ]; then
      if lsof -ti :8443 >/dev/null 2>&1; then
        echo "  https 后端已在 :8443"
      else
        nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8443 \
          --ssl-certfile certs/server.crt --ssl-keyfile certs/server.key \
          > "$LOG_DIR/backend-https.log" 2>&1 < /dev/null &
        echo $! > "${BACKEND_PID_FILE}.https"
        echo "  https 后端已起 :8443（真机媒体用）"
      fi
    fi
  fi
  curl -sf --noproxy '*' -m 3 "$BACKEND_URL" > /dev/null && echo "后端 OK: $BACKEND_URL" || { echo "✗ 后端未就绪，查看 $LOG_DIR/backend.log"; exit 1; }

  echo "===== [4/4] 管理端前端（:5173） ====="
  if is_running "$FRONTEND_PID_FILE"; then
    echo "前端已在运行（pid $(cat "$FRONTEND_PID_FILE")）"
  else
    (cd admin-web && [ -d node_modules ] || pnpm install --silent)
    nohup pnpm --dir admin-web dev > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
    echo $! > "$FRONTEND_PID_FILE"
    for _ in $(seq 1 30); do
      curl -sf --noproxy '*' -m 3 "$FRONTEND_URL" > /dev/null && break
      sleep 1
    done
  fi
  curl -sf --noproxy '*' -m 3 "$FRONTEND_URL" > /dev/null && echo "前端 OK: $FRONTEND_URL" || { echo "✗ 前端未就绪，查看 $LOG_DIR/frontend.log"; exit 1; }

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
  for pid_file in "$BACKEND_PID_FILE" "$BACKEND_PID_FILE.https" "$FRONTEND_PID_FILE"; do
    if is_running "$pid_file"; then
      pid=$(cat "$pid_file")
      # 杀进程组（vite 会 spawn 子进程）
      pkill -P "$pid" 2>/dev/null || true
      kill "$pid" 2>/dev/null || true
      echo "已停止 pid ${pid}（${pid_file}）"
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
