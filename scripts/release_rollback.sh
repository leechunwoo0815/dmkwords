#!/usr/bin/env bash
# ============================================================
# DmkWords 一键回滚（B0-2，2026-11-26 双审查）
#
# 回滚到 `release_snapshot.sh` 打出的某个版本：
#   1. 先校验收到的 MANIFEST 里的 sha256（快照本身损坏就别回滚）；
#   2. **先给当前状态再拍一张快照**——回滚本身也可回滚（这条最容易漏，出事时最要命）；
#   3. 解包 → rsync 覆盖 backend/、admin-web/dist/、deploy/（dist 用 --delete，避免残留旧 chunk）；
#   4. restart 后端 → 自检 /health（200 才算成功）与 /ready（503 会明确报出来）；
#   5. **不动数据库**：迁移降级是独立决定，脚本只把快照时的 alembic 版本打印出来对照。
#
# 用法:
#   bash scripts/release_rollback.sh <label>            # 真正回滚（需 sudo -n systemctl）
#   bash scripts/release_rollback.sh <label> --dry-run  # 只校验 + 打印计划
#   bash scripts/release_rollback.sh --list             # 列出可用快照
# ============================================================

set -Eeuo pipefail

DMK_ROOT="${DMK_ROOT:-/opt/dmkwords}"
DMK_SERVICE="${DMK_SERVICE:-dmkwords-backend}"
DMK_HEALTH_URL="${DMK_HEALTH_URL:-http://127.0.0.1:8002/health}"
DMK_READY_URL="${DMK_READY_URL:-http://127.0.0.1:8002/ready}"
RELEASES_DIR="$DMK_ROOT/backups/releases"

DRY_RUN=0
LABEL=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --list)
      [ -d "$RELEASES_DIR" ] || { echo "（还没有任何快照：$RELEASES_DIR 不存在）"; exit 0; }
      for d in $(ls -1dt "$RELEASES_DIR"/*/ 2>/dev/null); do
        label="$(basename "$d")"
        info="$(grep -E '^(created_at|alembic_current)=' "$d/MANIFEST" 2>/dev/null | tr '\n' ' ' || true)"
        echo "$label  $info"
      done
      exit 0 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    -*) echo "未知参数: $arg" >&2; exit 64 ;;
    *) LABEL="$arg" ;;
  esac
done

log() { echo "[rollback $(date '+%H:%M:%S')] $*"; }
_sha256_check() {  # CentOS: sha256sum -c；macOS: shasum -a 256 -c
  if command -v sha256sum >/dev/null 2>&1; then sha256sum -c "$1"; else shasum -a 256 -c "$1"; fi
}
die() { echo "✗ $*" >&2; exit 1; }
run() { if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] $*"; else eval "$@"; fi }

[ -n "$LABEL" ] || die "用法：bash scripts/release_rollback.sh <label> [--dry-run]（--list 看有哪些）"
SNAP="$RELEASES_DIR/$LABEL"
[ -d "$SNAP" ] || die "快照不存在：${SNAP}（用 --list 看可用列表）"
[ -f "$SNAP/MANIFEST" ] || die "快照缺 MANIFEST：$SNAP"

# ---- 1. 校验快照完整性 ----
log "校验 $LABEL 的包校验和…"
if [ -f "$SNAP/MANIFEST" ]; then
  (
    cd "$SNAP"
    grep -A99 -- '--- sha256 ---' MANIFEST | grep '\.tar\.gz$' > /tmp/dkm_rollback_sums.$$ || true
    if [ -s /tmp/dkm_rollback_sums.$$ ]; then
      _sha256_check /tmp/dkm_rollback_sums.$$ || { rm -f /tmp/dkm_rollback_sums.$$; die "快照包校验失败：$SNAP 已损坏，不要回滚"; }
    else
      log "⚠ MANIFEST 没有校验和段（旧格式快照），跳过校验"
    fi
    rm -f /tmp/dkm_rollback_sums.$$
  )
fi

alembic_at_snapshot="$(grep -E '^alembic_current=' "$SNAP/MANIFEST" | cut -d= -f2- || true)"
log "快照当时的迁移版本：${alembic_at_snapshot:-(未知)}"
log "当前迁移版本：$(cd "$DMK_ROOT" && "${DMK_PYTHON:-.venv/bin/python}" -m alembic current 2>/dev/null | tail -1 || echo '(读取失败)')"
log "注意：本脚本**不动数据库**。若之后跑过新迁移，回滚代码前需单独决定是否 alembic downgrade。"

# ---- 2. 先给当前状态拍快照（回滚也可回滚）----
pre_label="pre-rollback-$(date +%Y%m%d-%H%M%S)"
log "先把当前状态快照为 ${pre_label}（回滚失败可再回来）"
run "DMK_ROOT='$DMK_ROOT' bash '$(dirname "$0")/release_snapshot.sh' '$pre_label'"

# ---- 3. 解包 + 覆盖 ----
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
run "tar -xzf '$SNAP/backend.tar.gz' -C '$tmpdir'"
run "mkdir -p '$tmpdir/dist' && tar -xzf '$SNAP/admin-dist.tar.gz' -C '$tmpdir/dist'"
run "mkdir -p '$tmpdir/deploy-root' && tar -xzf '$SNAP/deploy.tar.gz' -C '$tmpdir/deploy-root'"

# backend/：不用 --delete（uploads/data 等运行期写入不能被打包内容以外的东西抹掉）
run "rsync -a '$tmpdir/backend/' '$DMK_ROOT/backend/'"
# dist：**必须 --delete**，否则旧 chunk 残留 → index.html 引用旧 hash（S2 产物漂移同族）
run "rsync -a --delete '$tmpdir/dist/' '$DMK_ROOT/admin-web/dist/'"
run "rsync -a '$tmpdir/deploy-root/deploy/' '$DMK_ROOT/deploy/'"

# ---- 4. 重启 + 自检 ----
if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY-RUN：计划 restart $DMK_SERVICE 并自检 $DMK_HEALTH_URL / $DMK_READY_URL"
  log "DRY-RUN 完成（未改动任何线上文件）"
  exit 0
fi

if [ "${DMK_SKIP_RESTART:-0}" = "1" ]; then
  log "DMK_SKIP_RESTART=1：跳过 restart 与自检（只还原文件；演练/测试用）"
  log "✓ 已还原文件到 ${LABEL}（当前状态另存为 ${pre_label}）"
  exit 0
fi

log "重启 ${DMK_SERVICE}…"
sudo -n systemctl restart "$DMK_SERVICE" || die "重启失败：请立刻看 journalctl -u $DMK_SERVICE"

health=""
for _ in $(seq 1 20); do
  health="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$DMK_HEALTH_URL" 2>/dev/null || echo 000)"
  [ "$health" = "200" ] && break
  sleep 1
done
ready="$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$DMK_READY_URL" 2>/dev/null || echo n/a)"

log "自检：/health=$health /ready=$ready"
if [ "$health" != "200" ]; then
  die "回滚后 /health 不是 200（${health}）——**立刻**用 $pre_label 再回滚一次：bash scripts/release_rollback.sh $pre_label"
fi
if [ "$ready" != "200" ]; then
  log "⚠ /ready 未就绪（${ready}）：服务活着但依赖/调度器有问题，看 curl $DMK_READY_URL 的 checks 段"
fi

log "✓ 已回滚到 ${LABEL}（当前状态另存为 ${pre_label}）"
