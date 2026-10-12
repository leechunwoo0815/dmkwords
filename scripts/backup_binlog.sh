#!/usr/bin/env bash
# ============================================================
# DmkWords binlog 增量备份脚本
# 用途: 每小时备份 binlog 文件，用于 PITR (Point-in-Time Recovery)
# 用法: crontab -e → 0 * * * * /path/to/dmkwords/scripts/backup_binlog.sh
# 依赖: 宿主 mysql 客户端（**宿主形态**）或 docker（**容器形态**，本项目生产/演练默认）
#
# ⚠️ 2026-10-12 审查 P1（本脚本此前"静默空转"，PITR 能力实际不存在）：
#   本项目的 MySQL 跑在容器里（`docker-compose.prod.yml`：`--log-bin=mysql-bin` + **命名卷**
#   `mysql_data_prod:/var/lib/mysql`），而脚本原先按 `$MYSQL_DATA_DIR/binlog.*` 在**宿主**上找文件：
#   ① 宿主根本没有那个目录（数据在容器卷内）；② 文件名前缀也不对（实际是 `mysql-bin.NNNNNN`）。
#   两个原因叠加 → 复制循环匹配 0 个文件、脚本仍以退出码 0 打印"binlog 增量备份完成"，
#   灾难当天才会发现没有可回放的 binlog。
#   现在：探测到容器（默认 `dmkwords-mysql-prod`）→ 走 `docker exec` 列文件 + `docker cp` 取回；
#   探测不到则按宿主形态（Homebrew 等）走原逻辑，两种前缀都收。
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
# 可配数据目录（宿主形态用；容器形态从容器卷里取，见下）
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-/opt/homebrew/var/mysql}"
# 容器形态（本项目生产/演练默认）：容器名与容器内数据目录
MYSQL_CONTAINER="${MYSQL_CONTAINER:-dmkwords-mysql-prod}"
CONTAINER_DATA_DIR="${CONTAINER_DATA_DIR:-/var/lib/mysql}"
if command -v docker >/dev/null 2>&1 && docker inspect "$MYSQL_CONTAINER" >/dev/null 2>&1; then
  MYSQL_MODE="container"
else
  MYSQL_MODE="host"
fi
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

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 模式: ${MYSQL_MODE}（容器: ${MYSQL_CONTAINER}）"

# 执行 SQL：容器形态走 docker exec（宿主不必装 mysql 客户端），宿主形态走本机 mysql
run_sql() {
  if [ "$MYSQL_MODE" = "container" ]; then
    docker exec -e MYSQL_PWD="${DB_PASSWORD:-}" "$MYSQL_CONTAINER" \
      mysql -uroot -N -e "$1"
  else
    MYSQL_PWD="${DB_PASSWORD:-}" mysql -h"${DB_HOST:-localhost}" -P"${DB_PORT:-3306}" \
      -u"${DB_USER:-root}" -N -e "$1"
  fi
}

# 刷新 binlog（开始新 binlog 文件）
run_sql "FLUSH BINARY LOGS;"

# 复制已关闭的 binlog 文件到备份目录（排除当前正在写入的）
# MySQL 9.x 用 SHOW BINARY LOG STATUS 替代 SHOW MASTER STATUS
# 注意：`SHOW BINARY LOG STATUS` 是 MySQL 8.4+/9.x 语法，8.0 会 1064 报错——必须容错，
# 否则 `set -euo pipefail` 会在这一行直接中断（这也是本脚本此前"看起来在跑、其实什么都没做"的一环）。
CURRENT_BINLOG=$(run_sql "SHOW BINARY LOG STATUS;" 2>/dev/null | awk '{print $1}' || true)
if [ -z "$CURRENT_BINLOG" ]; then
  CURRENT_BINLOG=$(run_sql "SHOW MASTER STATUS;" 2>/dev/null | awk '{print $1}' || true)
fi

# 取文件清单（两种前缀都收：本项目 compose 用 mysql-bin.*，历史/默认配置可能是 binlog.*）
list_binlogs() {
  if [ "$MYSQL_MODE" = "container" ]; then
    docker exec "$MYSQL_CONTAINER" sh -c "ls $CONTAINER_DATA_DIR" 2>/dev/null |
      grep -E '^(mysql-bin|binlog)\.[0-9]+$' || true
  else
    ls "$MYSQL_DATA_DIR" 2>/dev/null | grep -E '^(mysql-bin|binlog)\.[0-9]+$' || true
  fi
}

fetch_binlog() {  # $1=文件名 $2=目标临时路径
  if [ "$MYSQL_MODE" = "container" ]; then
    docker cp "$MYSQL_CONTAINER:$CONTAINER_DATA_DIR/$1" "$2"
  else
    cp "$MYSQL_DATA_DIR/$1" "$2"
  fi
}

COPIED=0
for filename in $(list_binlogs); do
  if [ "$filename" != "$CURRENT_BINLOG" ]; then
    if [ ! -f "$BACKUP_DIR/$filename" ]; then
      # A-P2-6：原子复制——临时文件 + mv 替换，中断不留半写损坏文件（PITR 可靠性）
      tmp_file="$BACKUP_DIR/.$filename.tmp.$$"
      if fetch_binlog "$filename" "$tmp_file"; then
        mv "$tmp_file" "$BACKUP_DIR/$filename"
        COPIED=$((COPIED + 1))
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复制 binlog: $filename"
      else
        rm -f "$tmp_file" 2>/dev/null || true
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 复制失败（跳过）: $filename" >&2
      fi
    fi
  fi
done
# 空转自证（2026-10-12）：本轮一个都没收到且备份目录为空 → 明确报警，不再"看起来成功"
if [ "$COPIED" -eq 0 ] && [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null | grep -Ev '^\.' || true)" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠ 未取到任何 binlog 且备份目录为空——PITR 无可用归档，请检查容器名/数据目录" >&2
  exit 2
fi

# 清理 RETENTION_DAYS 天前的 binlog 备份（L3-042：对齐全量保留，闭锁 PITR 窗口）
find "$BACKUP_DIR" \( -name "binlog.*" -o -name "mysql-bin.*" \) -mtime +"$RETENTION_DAYS" -delete 2>/dev/null
echo "[$(date '+%Y-%m-%d %H:%M:%S')] binlog 增量备份完成"
