#!/usr/bin/env bash
# scripts/mysqlbinlog_via_docker.sh — 用一次性容器里的 mysqlbinlog 跑 binlog 重放（PITR 演练用）
#
# 为什么需要它：官方 `mysql:8.0` 镜像**不含 mysqlbinlog**（只装 mysql-community-server-minimal），
# 宿主也可能没装 mysql-client（brew 从 ghcr.io 拉极慢）。而 OL9 AppStream 的 `mysql` 包（~2.9MB）
# **自带 `/usr/bin/mysqlbinlog`**——本脚本配合下面的镜像构建，把工具装进一个一次性容器里。
#
# 一次性准备（本机已执行过，镜像名 `dmkwords-mysqlbinlog:local`）：
#   docker build -t dmkwords-mysqlbinlog:local - <<'EOF'
#   FROM mysql:8.0
#   RUN microdnf install -y cpio >/dev/null 2>&1 \
#    && cd /tmp && microdnf download mysql >/dev/null 2>&1 \
#    && rpm2cpio mysql-*.rpm | cpio -idm --quiet ./usr/bin/mysqlbinlog \
#    && mv /tmp/usr/bin/mysqlbinlog /usr/local/bin/mysqlbinlog && chmod +x /usr/local/bin/mysqlbinlog \
#    && rm -rf /tmp/mysql-*.rpm /tmp/usr && microdnf clean all >/dev/null 2>&1
#   ENTRYPOINT ["/usr/local/bin/mysqlbinlog"]
#   EOF
#   docker run --rm dmkwords-mysqlbinlog:local --version   # 应打印 Ver 8.0.x
#
# 用法（供 `scripts/restore_drill.py` 的 `REPLAY_MODE=host` 调用；它按普通可执行文件传参）：
#   MYSQLBINLOG_BIN="$PWD/scripts/mysqlbinlog_via_docker.sh" REPLAY_MODE=host APPLY=1 \
#     python -m scripts.restore_drill pitr
#
# 语义：把参数里那个**绝对路径的 binlog 文件**的目录挂到容器 `/binlog`，其余参数原样透传；
# 输出到 stdout（调用方管道进 `docker exec -i … mysql`）。镜像可用 `MYSQLBINLOG_IMAGE` 覆盖。
set -euo pipefail

IMG="${MYSQLBINLOG_IMAGE:-dmkwords-mysqlbinlog:local}"
args=()
dir=""
for a in "$@"; do
  case "$a" in
    /*)
      dir="$(dirname "$a")"
      args+=("/binlog/$(basename "$a")")
      ;;
    *) args+=("$a") ;;
  esac
done

if [ -z "$dir" ]; then
  echo "用法：$0 [mysqlbinlog 参数…] <绝对路径的 binlog 文件>" >&2
  exit 2
fi

if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "镜像 $IMG 不存在——按本文件头部的 docker build 一次性构建" >&2
  exit 3
fi

exec docker run --rm -i -v "${dir}:/binlog:ro" "$IMG" "${args[@]}"
