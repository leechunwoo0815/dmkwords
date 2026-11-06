#!/usr/bin/env bash
# ============================================================
# DmkWords 数据库全量备份脚本（每日）
#
# 用法:
#   crontab: 10 3 * * * cd /opt/dmkwords && bash scripts/backup_db.sh >> backups/backup.log 2>&1
#   手动:   bash scripts/backup_db.sh
#
# 2026-11-06 重做（生产宿主 MySQL 实测），四个关键点：
#   1. `--no-tablespaces`：生产账号没有全局 PROCESS，缺此参数 mysqldump 会报
#      "Access denied ... PROCESS privilege ... when trying to dump tablespaces" 并非零退出；
#      旧脚本还会留下**截断的 .gz**（gzip -t 照样通过，灾难时才发现不可恢复）。
#   2. **原子写**：先写 .tmp，三重校验通过才 mv 成正式文件；失败即删临时文件。
#   3. **完整性三重校验**：gzip -t + 末行 `-- Dump completed` + `CREATE TABLE` 数量对拍。
#   4. 凭证优先 `--defaults-extra-file`（`.backup.cnf`，600），密码不进命令行；
#      没有 .backup.cnf 时回退 `.env` 的 DB_*（开发机用法不变）。
#
# 保留策略（可 env 覆盖）:
#   BACKUP_RETENTION_DAYS=14       日备保留天数
#   BACKUP_WEEKLY_RETENTION_DAYS=28 周备（周日）保留天数
#   BACKUP_UPLOADS=1               是否同时备份 uploads/
# ============================================================

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

# A-P2-5：禁止 source .env（任意命令注入即 RCE）——只安全提取需要的键
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

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
WEEKLY_RETENTION_DAYS="${BACKUP_WEEKLY_RETENTION_DAYS:-28}"
BACKUP_UPLOADS="${BACKUP_UPLOADS:-1}"
MYSQL_CNF="${MYSQL_CNF:-$PROJECT_DIR/.backup.cnf}"

DB_HOST="${DB_HOST:-$(load_env_val DB_HOST)}"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-$(load_env_val DB_PORT)}"; DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-$(load_env_val DB_USER)}"; DB_USER="${DB_USER:-dmkwords}"
DB_PASSWORD="${DB_PASSWORD:-$(load_env_val DB_PASSWORD)}"
DB_NAME="${DB_NAME:-$(load_env_val DB_NAME)}"; DB_NAME="${DB_NAME:-dmkwords}"

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

run_mysqldump() {
  if [ "$USE_CNF" = "1" ]; then
    mysqldump --defaults-extra-file="$MYSQL_CNF" "$@"
  else
    MYSQL_PWD="$DB_PASSWORD" mysqldump --host="$DB_HOST" --port="$DB_PORT" --user="$DB_USER" "$@"
  fi
}

mkdir -p "$BACKUP_DIR/weekly"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── 单实例锁（mkdir 原子；崩溃残留按 PID 存活判断）──
LOCK_DIR="$BACKUP_DIR/.backup.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -f "$LOCK_DIR/pid" ] && kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
    log "已有备份在运行（pid $(cat "$LOCK_DIR/pid")），本次退出"
    exit 0
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
echo $$ > "$LOCK_DIR/pid"

DATE_TAG="$(date +%Y%m%d_%H%M%S)"
TMP_SQL="$BACKUP_DIR/.tmp_${DATE_TAG}_$$.sql.gz"
TMP_UPLOADS="$BACKUP_DIR/.tmp_uploads_${DATE_TAG}_$$.tar.gz"
cleanup() {
  rm -f "$TMP_SQL" "$TMP_UPLOADS"
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT

log "开始全量备份: $DB_NAME -> $BACKUP_DIR"

EXPECTED_TABLES="$(run_mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME' AND table_type='BASE TABLE'")"
if [ -z "$EXPECTED_TABLES" ] || [ "$EXPECTED_TABLES" -lt 1 ]; then
  log "❌ 无法读取 $DB_NAME 的表数量（库不存在或权限不足）"
  exit 1
fi

if ! run_mysqldump --single-transaction --quick --routines --triggers --events \
  --set-gtid-purged=OFF --column-statistics=0 --no-tablespaces \
  --default-character-set=utf8mb4 "$DB_NAME" | gzip -6 > "$TMP_SQL"; then
  log "❌ mysqldump 失败（临时文件已删除）"
  exit 1
fi

# 校验 1：压缩流完整
if ! gzip -t "$TMP_SQL" 2>/dev/null; then
  log "❌ gzip 完整性校验失败"
  exit 1
fi

# 校验 2+3：结束标记 + 表数量（一次解压同时统计）
STATS="$(zcat "$TMP_SQL" | awk '/^CREATE TABLE/{n++} {last=$0} END{printf "%d\t%s", n, last}')"
TABLE_COUNT="${STATS%%$'\t'*}"
LAST_LINE="${STATS#*$'\t'}"
if [ "$TABLE_COUNT" != "$EXPECTED_TABLES" ]; then
  log "❌ 表数量不符：dump=${TABLE_COUNT}，库内=${EXPECTED_TABLES}（疑似截断）"
  exit 1
fi
case "$LAST_LINE" in
  "-- Dump completed"*) ;;
  *) log "❌ 缺少 mysqldump 结束标记（末行：${LAST_LINE:-<空>}）"; exit 1 ;;
esac

BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_full_${DATE_TAG}.sql.gz"
mv "$TMP_SQL" "$BACKUP_FILE"
chmod 600 "$BACKUP_FILE"
BACKUP_SIZE="$(du -h "$BACKUP_FILE" | awk '{print $1}')"
log "✅ 全量备份完成: $BACKUP_FILE ($BACKUP_SIZE, $TABLE_COUNT 表)"

# 周日额外留周备（日备只留 RETENTION_DAYS 天，周备覆盖更久）
if [ "$(date +%u)" = "7" ]; then
  cp -p "$BACKUP_FILE" "$BACKUP_DIR/weekly/$(basename "$BACKUP_FILE")"
  log "✅ 周备已保留: weekly/$(basename "$BACKUP_FILE")"
fi

# ── uploads 文件备份（可选；失败不阻塞数据库备份）──
if [ "$BACKUP_UPLOADS" = "1" ] && [ -d "$PROJECT_DIR/uploads" ]; then
  if tar -czf "$TMP_UPLOADS" -C "$PROJECT_DIR" uploads/ && tar -tzf "$TMP_UPLOADS" >/dev/null 2>&1; then
    UPLOADS_ARCHIVE="$BACKUP_DIR/uploads_${DATE_TAG}.tar.gz"
    mv "$TMP_UPLOADS" "$UPLOADS_ARCHIVE"
    chmod 600 "$UPLOADS_ARCHIVE"
    log "✅ 文件备份完成: $UPLOADS_ARCHIVE ($(du -h "$UPLOADS_ARCHIVE" | awk '{print $1}'))"
  else
    log "⚠ uploads 备份失败（不阻塞数据库备份）"
    rm -f "$TMP_UPLOADS"
  fi
fi

# ── 清理过期 ──
DELETED_DB="$(find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}_full_*.sql.gz" -mtime +"$RETENTION_DAYS" -print -delete 2>/dev/null | wc -l)"
DELETED_WEEKLY="$(find "$BACKUP_DIR/weekly" -name "${DB_NAME}_full_*.sql.gz" -mtime +"$WEEKLY_RETENTION_DAYS" -print -delete 2>/dev/null | wc -l)"
DELETED_UPLOADS="$(find "$BACKUP_DIR" -maxdepth 1 -name "uploads_*.tar.gz" -mtime +"$RETENTION_DAYS" -print -delete 2>/dev/null | wc -l)"
log "清理过期备份: 日备 ${DELETED_DB} / 周备 ${DELETED_WEEKLY} / 文件 ${DELETED_UPLOADS}"

# ── 元数据（便于事后核对；不做判据）──
META_FILE="$BACKUP_DIR/backup_log.csv"
[ -f "$META_FILE" ] || echo "timestamp,file,size,tables,db_size_mb" > "$META_FILE"
DB_SIZE="$(run_mysql -N -e "SELECT ROUND(SUM(data_length+index_length)/1024/1024,2) FROM information_schema.tables WHERE table_schema='$DB_NAME'" 2>/dev/null || echo NA)"
echo "${DATE_TAG},$(basename "$BACKUP_FILE"),$BACKUP_SIZE,$TABLE_COUNT,${DB_SIZE:-NA}" >> "$META_FILE"

log "全量备份流程结束"
