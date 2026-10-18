#!/usr/bin/env bash
# scripts/disk_alert.sh — 磁盘水位告警（P3-5，2026-10-18 第七十七批）
#
# 为什么重写：旧 cron 行是 `df -P /opt | awk '… strftime(…)'`——`strftime()` 是 **gawk 扩展**，
# Debian/Ubuntu 默认 `mawk` 下直接报错（**等于没告警**）；且只写 `/var/log/` 无人看。
# 本脚本：① 纯 bash，无 awk 扩展依赖；② 超阈值时**推送到人**（webhook）；
# ③ 没配 webhook 就**非零退出**，让 cron 的 MAILTO 兜底——不允许"静默失败"。
#
# 用法（crontab）：*/30 * * * * /opt/dmkwords/scripts/disk_alert.sh
# 环境变量：DISK_ALERT_PERCENT（默认 85）· DISK_ALERT_PATH（默认 /opt）·
#          DISK_ALERT_WEBHOOK（企业微信/钉钉机器人）· DISK_ALERT_LOG_DIR（默认 /opt/dmkwords/.dev-logs）
set -euo pipefail

THRESHOLD="${DISK_ALERT_PERCENT:-85}"
TARGET="${DISK_ALERT_PATH:-/opt}"
LOG_DIR="${DISK_ALERT_LOG_DIR:-/opt/dmkwords/.dev-logs}"
mkdir -p "$LOG_DIR"

USED=$(df -P "$TARGET" | tail -1 | tr -s ' ' | cut -d' ' -f5 | tr -d '%')
if [ "${USED:-0}" -le "$THRESHOLD" ]; then
  exit 0
fi

MSG="[DmkWords] $TARGET 磁盘水位 ${USED}% > ${THRESHOLD}%（音频上百 GB 打满风险，OSS 迁移预案 ADR-004）"
echo "$(date '+%F %T') $MSG" >> "$LOG_DIR/disk-alert.log"

if [ -n "${DISK_ALERT_WEBHOOK:-}" ]; then
  curl -sS -m 10 -H 'Content-Type: application/json' \
    -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$MSG\"}}" "$DISK_ALERT_WEBHOOK" >/dev/null
  exit 0
fi

echo "$(date '+%F %T') 未配 DISK_ALERT_WEBHOOK，仅落日志（非零退出交给 cron MAILTO）" \
  >> "$LOG_DIR/disk-alert.log"
exit 1
