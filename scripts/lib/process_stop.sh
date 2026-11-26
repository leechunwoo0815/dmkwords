#!/usr/bin/env bash
# scripts/lib/process_stop.sh — 停进程的**等待语义**（被 dev.sh source）
#
# 为什么单独抽出来（复审 N1，2026-11-26）：
# 原先 `dev.sh restart` 是 `stop; start`，而 `stop()` 发完 SIGTERM 就**立刻**删 pid 文件返回。
# uvicorn 在还有 keep-alive/长连接时不会马上退（实测 SIGTERM 后数分钟不退），于是紧接着的
# `start()` 看到 8002 仍被占 → 要么报"端口被占用"退出，要么健康检查打到**旧进程**上假装 OK。
# 后果是 `check_demo_baseline` 里那次内部 restart **确定性假红**（专家复现两次，均靠 kill -9 收场）。
#
# 这里把"发信号 → 等它真退 → 超时才 SIGKILL → 再等端口释放"做成可复用的两段，
# 上层脚本（dev.sh）只管调用；等待时长可调，默认给 10 秒（uvicorn 收尾通常 < 1 秒）。

# 等一个 pid 消失（僵尸由调用方 kill -0 判定；macOS/Linux 通用）
wait_pid_gone() {
  local pid="$1" timeout="${2:-10}" ticks=0 max_ticks=$(( ${2:-10} * 5 ))
  while kill -0 "$pid" 2>/dev/null; do
    [ "$ticks" -ge "$max_ticks" ] && return 1
    sleep 0.2
    ticks=$((ticks + 1))
  done
  return 0
}

# stop_pid_gracefully <pid> [timeout_seconds]
#   SIGTERM → 等 timeout 秒 → 不退则 SIGKILL → 再等 3 秒。返回 0 = 已确认退出。
stop_pid_gracefully() {
  local pid="$1" timeout="${2:-10}"
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM "$pid" 2>/dev/null || true
  if wait_pid_gone "$pid" "$timeout"; then
    return 0
  fi
  echo "  ⚠ pid $pid 在 ${timeout}s 内未退出（SIGTERM 被阻塞），改用 SIGKILL"
  kill -KILL "$pid" 2>/dev/null || true
  wait_pid_gone "$pid" 3
}

# wait_port_free <port> [timeout_seconds]：确认端口真的释放（TIME_WAIT 不算占用，lsof 只列 LISTEN 持有者）
wait_port_free() {
  local port="$1" timeout="${2:-10}" waited=0
  while lsof -ti "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1; do
    [ "$waited" -ge $((timeout * 5)) ] && return 1
    sleep 0.2
    waited=$((waited + 1))
  done
  return 0
}
