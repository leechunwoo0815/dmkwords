#!/usr/bin/env bash
# ============================================================
# DmkWords binlog 增量备份（每小时，PITR 用）
#
# 用法:
#   crontab: 5 * * * * cd /opt/dmkwords && bash scripts/backup_binlog.sh >> backups/binlog.log 2>&1
#   手动:   bash scripts/backup_binlog.sh
#
# 三种取数模式（BACKUP_BINLOG_MODE 显式指定，未指定时自动探测）:
#   remote    — `mysqlbinlog --read-from-remote-server --raw`（**推荐**：不依赖服务器文件读权限，
#               腾讯云 CDB / RDS 这类拿不到数据目录的托管库也能用）
#   files     — 直接复制宿主机数据目录（需 root 或读权限）
#   container — docker exec / docker cp（旧行为，dev 的 Docker MySQL）
#
# 权限（remote 模式的专用备份账号）:
#   RELOAD             FLUSH BINARY LOGS
#   REPLICATION CLIENT SHOW BINARY LOGS / SHOW MASTER STATUS
#   REPLICATION SLAVE  --read-from-remote-server 拉流
#
# 凭证优先 `.backup.cnf`（[client] host/port/user/password，600）；否则回退 `.env` 的 DB_*。
#
# 2026-11-06 实测背景：生产宿主 MySQL 下，dmkwords 账号没有 REPLICATION CLIENT，
# 旧脚本 `SHOW MASTER STATUS` 会失败；且读 `/var/lib/mysql` 需要 root。remote 模式一次解决两个问题。
# ============================================================

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

# A-P2-6：同 backup_db.sh——禁止 source .env（注入即 RCE），安全提取本脚本需要的键
load_env_val() {
  local key="$1"
  [ -f "$ENV_FILE" ] || return 0
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

BACKUP_BASE="${BACKUP_DIR:-$PROJECT_DIR/backups}"
BACKUP_DIR="${BACKUP_BASE}/binlog"
RETENTION_DAYS="${BACKUP_BINLOG_RETENTION_DAYS:-${BACKUP_RETENTION_DAYS:-14}}"
MYSQL_CNF="${MYSQL_CNF:-$PROJECT_DIR/.backup.cnf}"
MYSQL_CONTAINER="${MYSQL_CONTAINER:-dmkwords-mysql-prod}"
CONTAINER_DATA_DIR="${CONTAINER_DATA_DIR:-/var/lib/mysql}"
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-/var/lib/mysql}"
DO_FLUSH="${BACKUP_BINLOG_FLUSH:-1}"

DB_HOST="${DB_HOST:-$(load_env_val DB_HOST)}"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-$(load_env_val DB_PORT)}"; DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-$(load_env_val DB_USER)}"; DB_USER="${DB_USER:-dmkwords}"
DB_PASSWORD="${DB_PASSWORD:-$(load_env_val DB_PASSWORD)}"

if [ -f "$MYSQL_CNF" ]; then
  USE_CNF=1
else
  USE_CNF=0
fi

run_mysql() {
  if [ "$USE_CNF" = "1" ]; then
    mysql --defaults-extra-file="$MYSQL_CNF" "$@"
  else
    MYSQL_PWD="$DB_PASSWORD" mysql --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" "$@"
  fi
}

run_mysqlbinlog() {
  if [ "$USE_CNF" = "1" ]; then
    mysqlbinlog --defaults-extra-file="$MYSQL_CNF" "$@"
  else
    MYSQL_PWD="$DB_PASSWORD" mysqlbinlog --host="$DB_HOST" --port="$DB_PORT" \
      --user="$DB_USER" "$@"
  fi
}

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── 模式探测 ──
MODE="${BACKUP_BINLOG_MODE:-}"
if [ -z "$MODE" ]; then
  if command -v docker >/dev/null 2>&1 && docker inspect "$MYSQL_CONTAINER" >/dev/null 2>&1; then
    MODE="container"
  elif [ -f "$MYSQL_CNF" ] && command -v mysqlbinlog >/dev/null 2>&1; then
    MODE="remote"
  else
    MODE="files"
  fi
fi
case "$MODE" in
  remote|files|container) ;;
  *) log "❌ 未知 BACKUP_BINLOG_MODE=$MODE"; exit 2 ;;
esac

mkdir -p "$BACKUP_DIR"
log "模式: ${MODE}（目录: ${BACKUP_DIR}）"

if [ "$MODE" = "remote" ]; then
  command -v mysqlbinlog >/dev/null 2>&1 || { log "❌ 缺 mysqlbinlog"; exit 2; }
fi

# ── 列出 binlog（两种前缀都收：binlog.* / mysql-bin.*）──
list_binlogs() {
  if [ "$MODE" = "container" ]; then
    docker exec "$MYSQL_CONTAINER" sh -c "ls $CONTAINER_DATA_DIR" 2>/dev/null |
      grep -E '^(mysql-bin|binlog)\.[0-9]+$' || true
  elif [ "$MODE" = "files" ]; then
    ls "$MYSQL_DATA_DIR" 2>/dev/null | grep -E '^(mysql-bin|binlog)\.[0-9]+$' || true
  else
    run_mysql -N -e "SHOW BINARY LOGS" 2>/dev/null | awk '{print $1}' |
      grep -E '^(mysql-bin|binlog)\.[0-9]+$' || true
  fi
}

# 远端文件大小（仅 remote 模式用于校验）
server_size() {
  [ "$MODE" = "remote" ] || { echo ""; return 0; }
  run_mysql -N -e "SHOW BINARY LOGS" 2>/dev/null | awk -v n="$1" '$1==n {print $2}'
}

# 复制一个 binlog 到目标目录（目录内文件名保持原名）
# 注意：`mysqlbinlog --raw --result-file=X` 会把**原 binlog 文件名追加**到 X（实测 2026-11-06），
# 所以这里传目录 + 结尾斜杠，产物固定为 `<dir>/<name>`。
fetch_binlog() {  # $1=文件名 $2=目标目录
  if [ "$MODE" = "container" ]; then
    docker cp "$MYSQL_CONTAINER:$CONTAINER_DATA_DIR/$1" "$2/$1"
  elif [ "$MODE" = "files" ]; then
    cp "$MYSQL_DATA_DIR/$1" "$2/$1"
  else
    run_mysqlbinlog --read-from-remote-server --raw --result-file="$2/" "$1" >/dev/null
  fi
}

# ── 刷新 binlog，把当前文件封口（需要 RELOAD）──
if [ "$DO_FLUSH" = "1" ]; then
  if ! run_mysql -e "FLUSH BINARY LOGS" >/dev/null 2>&1; then
    log "❌ FLUSH BINARY LOGS 失败（备份账号缺 RELOAD 权限？）"
    exit 2
  fi
fi

ALL_LOGS="$(list_binlogs)"
if [ -z "$ALL_LOGS" ]; then
  log "❌ 取不到任何 binlog 列表（权限/模式不对）"
  exit 2
fi
CURRENT_BINLOG="$(echo "$ALL_LOGS" | tail -1)"

COPIED=0
for filename in $ALL_LOGS; do
  [ "$filename" = "$CURRENT_BINLOG" ] && continue          # 正在写的文件不取
  [ -f "$BACKUP_DIR/$filename" ] && continue               # 已备过
  tmp_dir="$(mktemp -d "$BACKUP_DIR/.tmp.XXXXXX")"
  if fetch_binlog "$filename" "$tmp_dir"; then
    actual="$(wc -c < "$tmp_dir/$filename" | tr -d ' ')"
    expected="$(server_size "$filename")"
    if [ -n "$expected" ] && [ "$actual" != "$expected" ]; then
      log "❌ $filename 大小不符：本地=$actual 远端=${expected}（丢弃）"
      rm -rf "$tmp_dir"
      exit 1
    fi
    mv "$tmp_dir/$filename" "$BACKUP_DIR/$filename"
    rm -rf "$tmp_dir"
    COPIED=$((COPIED + 1))
    log "✅ 复制 binlog: ${filename}（$actual 字节）"
  else
    rm -rf "$tmp_dir"
    log "❌ 复制失败: $filename"
    exit 1
  fi
done

# 空转自证：一个都没取到且目录为空 → 明确报警，不再"看起来成功"
if [ "$COPIED" -eq 0 ] && [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null | grep -Ev '^\.' || true)" ]; then
  log "⚠ 未取到任何 binlog 且备份目录为空——PITR 无可用归档，请检查模式/权限/数据目录"
  exit 2
fi

# 清理过期
DELETED="$(find "$BACKUP_DIR" \( -name "binlog.*" -o -name "mysql-bin.*" \) -mtime +"$RETENTION_DAYS" -print -delete 2>/dev/null | wc -l)"
log "本次新增 $COPIED 个；清理过期 $DELETED 个（保留 ${RETENTION_DAYS} 天）"
log "binlog 增量备份完成"
