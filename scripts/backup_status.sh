#!/usr/bin/env bash
# ============================================================
# DmkWords 备份健康检查（只读，不产生备份）
#
# 判据:
#   1. 最新全量备份的 mtime <= BACKUP_FULL_MAX_AGE_HOURS（默认 26 小时）
#   2. 最新 binlog 备份的 mtime <= BACKUP_BINLOG_MAX_AGE_HOURS（默认 2 小时）
#   3. 最新全量能被 gzip -t 打开
#
# 用法:
#   bash scripts/backup_status.sh
#   crontab: 15 8 * * * cd /opt/dmkwords && bash scripts/backup_status.sh >> backups/status.log 2>&1
#
# 告警: 配 BACKUP_ALERT_WEBHOOK（企业微信/钉钉机器人）时推送文本消息；
#       未配则非零退出（交给 cron 的 MAILTO/外部监控）。
# ============================================================

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
FULL_MAX_AGE_HOURS="${BACKUP_FULL_MAX_AGE_HOURS:-26}"
BINLOG_MAX_AGE_HOURS="${BACKUP_BINLOG_MAX_AGE_HOURS:-2}"
WEBHOOK="${BACKUP_ALERT_WEBHOOK:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

mtime() {  # 跨 macOS/Linux
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null
}

now="$(date +%s)"
problems=""

latest_full="$(ls -t "$BACKUP_DIR"/dmkwords_full_*.sql.gz 2>/dev/null | head -1 || true)"
if [ -z "$latest_full" ]; then
  problems="${problems}无全量备份；"
else
  age_h=$(( (now - $(mtime "$latest_full")) / 3600 ))
  if [ "$age_h" -gt "$FULL_MAX_AGE_HOURS" ]; then
    problems="${problems}全量备份已 ${age_h}h（阈值 ${FULL_MAX_AGE_HOURS}h）；"
  fi
  gzip -t "$latest_full" 2>/dev/null || problems="${problems}最新全量 gzip 校验失败；"
  log "最新全量: $(basename "$latest_full")，${age_h}h 前"
fi

latest_binlog="$(ls -t "$BACKUP_DIR"/binlog/binlog.* "$BACKUP_DIR"/binlog/mysql-bin.* 2>/dev/null | head -1 || true)"
if [ -z "$latest_binlog" ]; then
  problems="${problems}无 binlog 备份；"
else
  bage_h=$(( (now - $(mtime "$latest_binlog")) / 3600 ))
  if [ "$bage_h" -gt "$BINLOG_MAX_AGE_HOURS" ]; then
    problems="${problems}binlog 备份已 ${bage_h}h（阈值 ${BINLOG_MAX_AGE_HOURS}h）；"
  fi
  log "最新 binlog: $(basename "$latest_binlog")，${bage_h}h 前"
fi

if [ -z "$problems" ]; then
  log "✅ 备份健康"
  exit 0
fi

log "❌ 备份异常: $problems"
if [ -n "$WEBHOOK" ]; then
  payload="{\"msgtype\":\"text\",\"text\":{\"content\":\"DmkWords 备份异常: ${problems}\"}}"
  curl -sS -m 10 -H 'Content-Type: application/json' -d "$payload" "$WEBHOOK" >/dev/null || true
fi
exit 2
