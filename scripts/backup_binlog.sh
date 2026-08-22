#!/usr/bin/env bash
# ============================================================
# DmkWords binlog 增量备份脚本
# 用途: 每小时备份 binlog 文件，用于 PITR (Point-in-Time Recovery)
# 用法: crontab -e → 0 * * * * /path/to/dmkwords/scripts/backup_binlog.sh
# 依赖: mysqlbinlog (Homebrew MySQL 自带)
# ============================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# A-P2-6：同 backup_db.sh——禁止 source .env（注入即 RCE），安全提取本脚本需要的键
ENV_FILE="$PROJECT_DIR/.env"
load_env_val() {
  local key="$1"
  if [ ! -f "$ENV_FILE" ]; then
    return 0
  fi
  awk -v k="$key" '
    {
      stripped = $0
      sub(/^[[:space:]]*export[[:space:]]+/, "", stripped)
      if (stripped ~ ("^" k "=")) {
        sub(/^[[:space:]]*[^=]+=/, "", stripped)
        gsub(/^["\x27]|["\x27]$/, "", stripped)
        print stripped
        exit
      }
    }
  ' "$ENV_FILE"
}

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}/binlog"
DB_HOST="${DB_HOST:-$(load_env_val DB_HOST)}"
DB_PORT="${DB_PORT:-$(load_env_val DB_PORT)}"
DB_USER="${DB_USER:-$(load_env_val DB_USER)}"
DB_PASSWORD="${DB_PASSWORD:-$(load_env_val DB_PASSWORD)}"
# L3-042：binlog 保留天数 ≥ 全量保留天数（backup_db.sh 默认 30 天）。
# 原 7 天导致 PITR 恢复窗口 = min(30,7) = 7 天，30 天全量中 23 天无对应 binlog。
# A-P2-6：env 变量 > .env 值 > 默认 30（保留 BACKUP_RETENTION_DAYS:-30 语义）
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(load_env_val BACKUP_RETENTION_DAYS)}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
# 可配数据目录（原硬编码 /opt/homebrew/var/mysql 换机即静默失效）
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-/opt/homebrew/var/mysql}"
# 兜底默认值（.env 缺失时）：env 变量 > .env 值 > 默认值
DB_HOST="${DB_HOST:-$(load_env_val DB_HOST)}"
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-$(load_env_val DB_PORT)}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-$(load_env_val DB_USER)}"
DB_USER="${DB_USER:-root}"
DB_PASSWORD="${DB_PASSWORD:-$(load_env_val DB_PASSWORD)}"
DB_PASSWORD="${DB_PASSWORD:-}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
DATE_TAG=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# 刷新 binlog（开始新 binlog 文件）
MYSQL_PWD="${DB_PASSWORD:-}" mysql -h"${DB_HOST:-localhost}" -P"${DB_PORT:-3306}" -u"${DB_USER:-root}" -e "FLUSH BINARY LOGS;"

# 复制已关闭的 binlog 文件到备份目录（排除当前正在写入的）
# MySQL 9.x 用 SHOW BINARY LOG STATUS 替代 SHOW MASTER STATUS
CURRENT_BINLOG=$(MYSQL_PWD="${DB_PASSWORD:-}" mysql -h"${DB_HOST:-localhost}" -P"${DB_PORT:-3306}" -u"${DB_USER:-root}" -N -e "SHOW BINARY LOG STATUS;" 2>/dev/null | awk '{print $1}')
if [ -z "$CURRENT_BINLOG" ]; then
  CURRENT_BINLOG=$(MYSQL_PWD="${DB_PASSWORD:-}" mysql -h"${DB_HOST:-localhost}" -P"${DB_PORT:-3306}" -u"${DB_USER:-root}" -N -e "SHOW MASTER STATUS;" 2>/dev/null | awk '{print $1}')
fi

for binlog_file in "$MYSQL_DATA_DIR"/binlog.*; do
  filename=$(basename "$binlog_file")
  if [ "$filename" != "$CURRENT_BINLOG" ]; then
    if [ ! -f "$BACKUP_DIR/$filename" ]; then
      # A-P2-6：原子复制——临时文件 + mv 替换，cp 中断不留半写损坏文件（PITR 可靠性）
      tmp_file="$BACKUP_DIR/.$filename.tmp.$$"
      if cp "$binlog_file" "$tmp_file"; then
        mv "$tmp_file" "$BACKUP_DIR/$filename"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复制 binlog: $filename"
      else
        rm -f "$tmp_file" 2>/dev/null || true
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复制失败（跳过）: $filename" >&2
      fi
    fi
  fi
done

# 清理 RETENTION_DAYS 天前的 binlog 备份（L3-042：对齐全量保留，闭锁 PITR 窗口）
find "$BACKUP_DIR" -name "binlog.*" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null
echo "[$(date '+%Y-%m-%d %H:%M:%S')] binlog 增量备份完成"
