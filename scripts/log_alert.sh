#!/usr/bin/env bash
# ============================================================
# DmkWords 日志异常巡检（默认最近 24 小时，只读）
#
# 判据:
#   1. MySQL 死锁（1213 / Deadlock found）> LOG_ALERT_DEADLOCK_MAX（默认 0）→ 报警
#      —— 这是 B2「活动结算/取消锁序」挂账的触发条件：生产一旦出现 1213 即应立项统一锁序。
#   2. 后端 500 响应 > LOG_ALERT_500_MAX（默认 5）→ 报警
#   3. 后端未捕获异常堆栈（Traceback）> 0 → 报警
#   4. **探测能力**：任一日志源读不到 / 窗口内 0 条 / MySQL 错误日志长期不更新
#      → 退出码 3（fail-closed）。B2-2（2026-11-26 双审查）：原先 `|| true` 把
#      "journalctl 没权限 / unit 名写错 / 日志被清" 全压成空串 → 三项计数 0 → 打
#      "✅ 巡检正常" 退出 0，等于监控瞎了还报平安。
#
# 退出码: 0 正常 / 2 发现异常 / 3 无法巡检（探测源不可用）
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
MYSQL_LOG="${LOG_ALERT_MYSQL_LOG:-/var/log/mysqld.log}"
MYSQL_LOG_MAX_AGE_DAYS="${LOG_ALERT_MYSQL_LOG_MAX_AGE_DAYS:-7}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

alert() {
  if [ -n "$WEBHOOK" ]; then
    curl -sS -m 10 -H 'Content-Type: application/json' \
      -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"DmkWords 日志巡检: $1\"}}" "$WEBHOOK" >/dev/null || true
  fi
}

# ---------- 探测能力自检（fail-closed） ----------

# 后端 journal（lcw 有 NOPASSWD sudo；脚本也可能以 root 运行）
if ! BACKEND_LOG="$(sudo -n journalctl -u dmkwords-backend --since "$SINCE" --no-pager 2>&1)"; then
  log "❌ 无法巡检：journalctl 读取 dmkwords-backend 失败——$(printf '%s\n' "$BACKEND_LOG" | tail -1)"
  alert "无法巡检（journalctl 读取失败），请检查 sudo 权限与 unit 名"
  exit 3
fi
# 真实日志行形如 `Nov 26 12:42:18 host unit[pid]: ...`；`-- No entries --` 之类不算记录
journal_entries="$(printf '%s\n' "$BACKEND_LOG" | grep -cE '^[A-Z][a-z]{2} +[0-9]{1,2} [0-9]{2}:[0-9]{2}:[0-9]{2} ' || true)"
if [ "$journal_entries" -eq 0 ]; then
  log "❌ 无法巡检：${SINCE} 内 dmkwords-backend 0 条日志（unit 名错 / 服务未启动 / journald 被清）"
  alert "无法巡检（后端日志窗口内 0 条），可能服务未启动"
  exit 3
fi

if ! MYSQL_ERR="$(sudo -n tail -n 50000 "$MYSQL_LOG" 2>&1)"; then
  log "❌ 无法巡检：读取 MySQL 错误日志 $MYSQL_LOG 失败——死锁探测失效"
  alert "无法巡检（MySQL 错误日志读不到），死锁探测失效"
  exit 3
fi
if [ -z "$(printf '%s' "$MYSQL_ERR" | tr -d '[:space:]')" ]; then
  log "❌ 无法巡检：MySQL 错误日志 $MYSQL_LOG 为空——死锁探测失效"
  alert "无法巡检（MySQL 错误日志为空），死锁探测失效"
  exit 3
fi
# 日志长期不更新：**不判红**（安静服务器本来就可能几周不写 error log，判红会假警），
# 但必须显式提示"死锁探测置信度下降"。要彻底可信，应改用 `Innodb_deadlocks` 计数器。
if mtime="$(sudo -n stat -c %Y "$MYSQL_LOG" 2>/dev/null)"; then
  age_days=$(( ( $(date +%s) - mtime ) / 86400 ))
  if [ "$MYSQL_LOG_MAX_AGE_DAYS" -gt 0 ] && [ "$age_days" -gt "$MYSQL_LOG_MAX_AGE_DAYS" ]; then
    # 复审（2026-11-26）更正：原先这里写"安静库属正常"，但当时生产 `innodb_print_all_deadlocks=OFF`，
    # 死锁**根本不写** error log —— "日志不动"是致盲不是安静。现在该开关已在生产打开并持久化
    # （见 docs/25 §三十三 同夜补充），所以"没有新行"确实可能只是没有错误。
    log "⚠ MySQL 错误日志 ${age_days} 天未更新（阈值 ${MYSQL_LOG_MAX_AGE_DAYS} 天）：已开 innodb_print_all_deadlocks，无错误时本就不会写；持续不更新时请核对 error_log 路径（可用 LOG_ALERT_MYSQL_LOG_MAX_AGE_DAYS=0 关闭提示）"
  fi
fi

# ---------- 计数 ----------

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
alert "日志异常: $problems"
exit 2
