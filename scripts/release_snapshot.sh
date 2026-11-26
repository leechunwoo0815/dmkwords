#!/usr/bin/env bash
# ============================================================
# DmkWords 发布锚点快照（B0-2，2026-11-26 双审查）
#
# 为什么需要：现在的部署是「rsync + restart」，回滚靠"记得上一个 commit 是哪个、再 rsync 一次"
# —— 生产目录**不是 git 工作树**（`/opt/dmkwords` 没有 .git），所以出事时没有权威的"上一版"
# 可以还原，只能靠人脑。本脚本在**每次部署前**把当前在跑的三块（backend / 管理端 dist / deploy
# 模板）打成带校验和的版本化包，回滚 = `release_rollback.sh <label>` 一条命令。
#
# 产物：$DMK_ROOT/backups/releases/<label>/
#         backend.tar.gz  admin-dist.tar.gz  deploy.tar.gz
#         MANIFEST  （校验和 + alembic 版本 + 当时的 /health 与 /ready 结果）
#         RESTORE.md
# 保留：默认只留最近 5 份（DMK_KEEP_RELEASES），更早的自动清理。
#
# 用法（在服务器上跑；本地可经 ssh 调用）:
#   bash scripts/release_snapshot.sh                    # label = 时间戳
#   bash scripts/release_snapshot.sh pre-180-batch      # 自定义 label
#   DMK_ROOT=/tmp/fakeroot bash scripts/release_snapshot.sh --dry-run
# ============================================================

set -Eeuo pipefail

DMK_ROOT="${DMK_ROOT:-/opt/dmkwords}"
DMK_KEEP_RELEASES="${DMK_KEEP_RELEASES:-5}"
DRY_RUN=0
LABEL=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    -*) echo "未知参数: $arg" >&2; exit 64 ;;
    *) LABEL="$arg" ;;
  esac
done

LABEL="${LABEL:-$(date +%Y%m%d-%H%M%S)}"
DEST="$DMK_ROOT/backups/releases/$LABEL"
SRC_BACKEND="$DMK_ROOT/backend"
SRC_DIST="$DMK_ROOT/admin-web/dist"
SRC_DEPLOY="$DMK_ROOT/deploy"

log() { echo "[snapshot $(date '+%H:%M:%S')] $*"; }
_sha256_files() {  # CentOS: sha256sum；macOS: shasum -a 256
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}
die() { echo "✗ $*" >&2; exit 1; }

run() {  # dry-run 时只打印
  if [ "$DRY_RUN" -eq 1 ]; then echo "  [dry-run] $*"; else eval "$@"; fi
}

[ -d "$SRC_BACKEND" ] || die "后端目录不存在：${SRC_BACKEND}（DMK_ROOT 指对了吗？）"
[ -d "$SRC_DIST" ] || die "管理端 dist 不存在：${SRC_DIST}（先构建再快照）"
[ -d "$SRC_DEPLOY" ] || die "deploy 目录不存在：$SRC_DEPLOY"
[ -e "$DEST" ] && die "快照已存在：${DEST}（换个 label，或先删掉旧的）"

log "目标快照：$DEST"
[ "$DRY_RUN" -eq 1 ] && log "DRY-RUN：只打印计划，不写盘"

# ---- 1. 打包三块 ----
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

run "mkdir -p '$DEST'"

# 只打"跑起来需要的东西"：dist 排除上传目录（uploads/avatars 是业务数据，不进发布包）
run "tar -czf '$DEST/backend.tar.gz' -C '$DMK_ROOT' backend"
run "tar -czf '$DEST/admin-dist.tar.gz' -C '$DMK_ROOT/admin-web/dist' --exclude=avatars --exclude=uploads --exclude=.DS_Store ."
run "tar -czf '$DEST/deploy.tar.gz' -C '$DMK_ROOT' deploy"

# ---- 2. 版本信息 ----
alembic_rev="(unknown)"
if [ -f "$DMK_ROOT/alembic.ini" ]; then
  if out="$(cd "$DMK_ROOT" && timeout 20 "${DMK_PYTHON:-.venv/bin/python}" -m alembic current 2>/dev/null)"; then
    alembic_rev="$(printf '%s' "$out" | tail -1)"
    [ -n "$alembic_rev" ] || alembic_rev="(empty)"
  else
    alembic_rev="(alembic 读取失败)"
  fi
fi

app_version="(unknown)"
if [ -r "$DMK_ROOT/backend/main.py" ] && command -v grep >/dev/null; then
  app_version="$(grep -m1 'APP_VERSION' "$DMK_ROOT/.env" 2>/dev/null | cut -d= -f2- || true)"
  app_version="${app_version:-(见 .env)}"
fi

health="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/health 2>/dev/null || echo "n/a")"
ready="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/ready 2>/dev/null || echo "n/a")"

if [ "$DRY_RUN" -eq 0 ]; then
  {
    echo "label=$LABEL"
    echo "created_at=$(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "host=$(hostname)"
    echo "root=$DMK_ROOT"
    echo "alembic_current=$alembic_rev"
    echo "app_version=$app_version"
    echo "health_at_snapshot=$health"
    echo "ready_at_snapshot=$ready"
    echo "backend_files=$(find "$SRC_BACKEND" -type f -name '*.py' | wc -l | tr -d ' ')"
    echo "dist_entries=$(find "$SRC_DIST" -maxdepth 1 -mindepth 1 | wc -l | tr -d ' ')"
    echo "--- sha256 ---"
    (cd "$DEST" && (_sha256_files ./*.tar.gz))
  } > "$DEST/MANIFEST"

  cat > "$DEST/RESTORE.md" <<RESTORE
# 回滚到本快照（${LABEL}）

\`\`\`bash
bash scripts/release_rollback.sh $LABEL
\`\`\`

- 会先把**当前状态**再快照一份（回滚本身也可回滚），再覆盖 backend/ + admin-web/dist/ + deploy/，然后 restart。
- **不动数据库**：迁移版本当时是 \`${alembic_rev}\`。若本版之后跑过新迁移，回滚代码前需单独决定是否 \`alembic downgrade\`。
- 回滚后自检：\`/health\` 200、\`/ready\` 200（快照时分别为 ${health} / ${ready}）。
RESTORE
fi

# ---- 3. 清理旧快照（只留最近 N 份）----
# 不用 mapfile：macOS 自带 bash 3.2 没有它（本脚本要能在开发机和 CentOS 7 上跑同一份）
if [ "$DRY_RUN" -eq 0 ] && [ "$DMK_KEEP_RELEASES" -gt 0 ]; then
  ls -1dt "$DMK_ROOT/backups/releases"/*/ 2>/dev/null | tail -n "+$((DMK_KEEP_RELEASES + 1))"     | while IFS= read -r old; do
        [ -n "$old" ] || continue
        log "清理旧快照：$(basename "$old")"
        rm -rf "$old"
      done
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "DRY-RUN 完成（未写任何文件）"
  exit 0
fi

log "✓ 快照完成：$DEST"
log "  backend / dist / deploy 三包 + MANIFEST + RESTORE.md"
grep -E '^(alembic_current|health_at_snapshot|ready_at_snapshot)=' "$DEST/MANIFEST" | sed 's/^/  /'
