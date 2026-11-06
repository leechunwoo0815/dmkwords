#!/usr/bin/env bash
# ============================================================
# DmkWords 日志异常巡检（默认最近 24 小时，只读）
#
# 判据:
#   1. MySQL 死锁（1213 / Deadlock found）> LOG_ALERT_DEADLOCK_MAX（默认 0）→ 报警
#      —— 这是 B2「活动结算/取消锁序」挂账的触发条件：生产一旦出现 1213 即应立项统一锁序。
#   2. 后端 500 响应 > LOG_ALERT_500_MAX（默认 5）→ 报警
#   3. 后端未捕获异常堆栈（Traceback）> 0 → 报警
#
# 用法:
#   bash scripts/log_alert.sh
#   crontab: 0 9 * * * cd /opt/dmkwords && bash scripts/log_alert.sh >> backups/log-alert.log 2>&1
#
# 告警: ALERT_WEBHOOK 或 BACKUP_ALERT_WEBHOOK（企业微信/钉钉机器人）；
#       未配则非零退出，交给 cron MAILTO / 外部监控。
# ============================================================

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SINCE="${LOG_ALERT_SINCE:-24 hours ago}"
WEBHOOK="${ALERT_WEBHOOK:-${BACKUP_ALERT_WEBHOOK:-}}"
DEADLOCK_MAX="${LOG_ALERT_DEADLOCK_MAX:-0}"
HTTP500_MAX="${LOG_ALERT_500_MAX:-5}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# 后端 journal（lcw 有 NOPASSWD sudo；脚本也可能以 root 运行）
BACKEND_LOG="$(sudo -n journalctl -u dmkwords-backend --since "$SINCE" --no-pager 2>/dev/null || true)"
MYSQL_ERR="$(sudo -n tail -n 50000 /var/log/mysqld.log 2>/dev/null || true)"

deadlocks="$(printf '%s\n%s\n' "$BACKEND_LOG" "$MYSQL_ERR" | grep -cE '1213|Deadlock found' || true)"
http500="$(printf '%s\n' "$BACKEND_LOG" | grep -cE '" 500 | 500 Internal Server Error' || true)"
tracebacks="$(printf '%s\n' "$BACKEND_LOG" | grep -c 'Traceback (most recent call last)' || true)"

log "最近 ${SINCE}：死锁 ${deadlocks} / 后端 500 ${http500} / Traceback ${tracebacks}"

problems=""
[ "$deadlocks" -gt "$DEADLOCK_MAX" ] && problems="${problems}死锁 ${deadlocks}（阈值 ${DEADLOCK_MAX}）；"
[ "$http500" -gt "$HTTP500_MAX" ] && problems="${problems}500 响应 ${http500}（阈值 ${HTTP500_MAX}）；"
[ "$tracebacks" -gt "0" ] && problems="${problems}未捕获异常 ${tracebacks} 处；"

if [ -z "$problems" ]; then
  log "✅ 日志巡检正常"
  exit 0
fi

log "❌ 日志异常: $problems"
if [ -n "$WEBHOOK" ]; then
  payload="{\"msgtype\":\"text\",\"text\":{\"content\":\"DmkWords 日志异常: ${problems}\"}}"
  curl -sS -m 10 -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK" >/dev/null || true
fi
exit 2
