#!/usr/bin/env bash
# ============================================================
# DmkWords 备份恢复演练（restore check）
#
# 做什么：把最新（或指定）全量备份恢复到**同一实例的临时库**，逐表对拍行数，
#         通过后删除临时库。只读业务库，不碰生产库。
#
# 用法:
#   sudo bash scripts/restore_check.sh                 # 用最新全量备份
#   sudo bash scripts/restore_check.sh <dump.sql.gz>   # 指定备份文件
#   KEEP=1 sudo bash scripts/restore_check.sh          # 演练后保留临时库供人工查看
#
# 安全阀:
#   1. 目标库名必须含 `restore_check`，且不得与业务库同名（DB_NAME）——写错也删不到生产库。
#   2. 只执行 DROP/CREATE 目标库；业务库只做 SELECT COUNT。
#   3. 需要 root（建库/导入）；凭证从 /root/.mysql-root-pw 或 RESTORE_CNF 读取。
#
# 建议频率：每周一次（cron 周日 04:30），或每次改 schema/迁移后手动跑一次。
# ============================================================

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

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
DB_HOST="${DB_HOST:-$(load_env_val DB_HOST)}"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-$(load_env_val DB_PORT)}"; DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-$(load_env_val DB_NAME)}"; DB_NAME="${DB_NAME:-dmkwords}"
RESTORE_DB="${RESTORE_DB:-${DB_NAME}_restore_check}"
RESTORE_CNF="${RESTORE_CNF:-/root/.mysql-restore.cnf}"
KEEP="${KEEP:-0}"
KEY_TABLES="${KEY_TABLES:-dictionary_words books parents children orders activities borrow_records notifications}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── 安全阀 ──
case "$RESTORE_DB" in
  *restore_check*) ;;
  *) log "❌ RESTORE_DB 必须包含 restore_check（当前：${RESTORE_DB}）"; exit 2 ;;
esac
if [ "$RESTORE_DB" = "$DB_NAME" ]; then
  log "❌ RESTORE_DB 不能等于业务库 $DB_NAME"
  exit 2
fi
case "$RESTORE_DB" in
  *[!A-Za-z0-9_]*) log "❌ RESTORE_DB 只允许字母/数字/下划线"; exit 2 ;;
esac

# ── 凭证（root）──
if [ -f "$RESTORE_CNF" ]; then
  USE_CNF=1
elif [ "$(id -u)" = "0" ] && [ -f /root/.mysql-root-pw ]; then
  USE_CNF=0
  MYSQL_PWD="$(cat /root/.mysql-root-pw)"
  export MYSQL_PWD
else
  log "❌ 需要 root（或提供 RESTORE_CNF=/path/to/root.cnf）"
  exit 2
fi

run_mysql() {
  if [ "$USE_CNF" = "1" ]; then
    mysql --defaults-extra-file="$RESTORE_CNF" "$@"
  else
    mysql --host="$DB_HOST" --port="$DB_PORT" --user=root "$@"
  fi
}

# ── 选备份文件 ──
DUMP="${1:-}"
case "$DUMP" in
  ""|--latest)
    DUMP="$(ls -t "$BACKUP_DIR"/${DB_NAME}_full_*.sql.gz 2>/dev/null | head -1 || true)"
    ;;
esac
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  log "❌ 找不到备份文件（目录：${BACKUP_DIR}）"
  exit 2
fi
log "演练备份: $DUMP ($(du -h "$DUMP" | awk '{print $1}'))"

# ── 备份文件自检 ──
gzip -t "$DUMP" 2>/dev/null || { log "❌ gzip 校验失败"; exit 1; }
LAST_LINE="$(zcat "$DUMP" | tail -n 1)"
case "$LAST_LINE" in
  "-- Dump completed"*) ;;
  *) log "❌ 备份缺少 mysqldump 结束标记（末行：${LAST_LINE:-<空>}）"; exit 1 ;;
esac

# ── 恢复 ──
log "恢复到临时库: $RESTORE_DB"
run_mysql -e "DROP DATABASE IF EXISTS \`$RESTORE_DB\`; CREATE DATABASE \`$RESTORE_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
if ! zcat "$DUMP" | run_mysql "$RESTORE_DB"; then
  log "❌ 恢复导入失败"
  exit 1
fi

# ── 对拍 ──
SRC_TABLES="$(run_mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME' AND table_type='BASE TABLE'")"
DST_TABLES="$(run_mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$RESTORE_DB' AND table_type='BASE TABLE'")"
log "表数量: 源=$SRC_TABLES 恢复=$DST_TABLES"

FAIL=0
[ "$SRC_TABLES" = "$DST_TABLES" ] || { log "❌ 表数量不一致"; FAIL=1; }

for t in $KEY_TABLES; do
  src_exists="$(run_mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME' AND table_name='$t'")"
  [ "$src_exists" = "1" ] || continue
  src_n="$(run_mysql -N -e "SELECT COUNT(*) FROM \`$DB_NAME\`.\`$t\`")"
  dst_n="$(run_mysql -N -e "SELECT COUNT(*) FROM \`$RESTORE_DB\`.\`$t\`")"
  if [ "$src_n" = "$dst_n" ]; then
    log "✅ $t: $src_n"
  else
    log "❌ $t: 源=$src_n 恢复=$dst_n"
    FAIL=1
  fi
done

if [ "$KEEP" = "1" ]; then
  log "临时库保留: ${RESTORE_DB}（KEEP=1）"
else
  run_mysql -e "DROP DATABASE IF EXISTS \`$RESTORE_DB\`"
  log "临时库已删除: $RESTORE_DB"
fi

if [ "$FAIL" != "0" ]; then
  log "❌ 恢复演练失败"
  exit 1
fi
log "✅ 恢复演练通过（备份可恢复且数据一致）"
