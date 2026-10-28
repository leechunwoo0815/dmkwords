# Dockerfile — DmkWords 后端镜像（第十轮 W3-02 修复，2026-10-24）
#
# 旧版两个硬伤（P2）：
#   ① `COPY requirements.txt .` —— 项目早已迁 `uv`/`pyproject.toml`，仓库里**没有**
#      requirements.txt ⇒ `docker build` 必挂（`COPY failed: file not found`）。
#   ② 全仓无 `.dockerignore` ⇒ `COPY . .` 一旦可执行，会把 `.env`（DB 密码/密钥/支付凭据）、
#      `certs/server.key`（HTTPS 私钥）、`backups/*.sql.gz`（真实业务数据 dump）打进镜像层。
# 现在：依赖走 `pyproject.toml` + `uv.lock`（`--frozen` 保证与门禁同一套锁），
#       构建上下文由 `.dockerignore` 收口，容器以非 root 用户运行，并带 `/health` 健康检查。
#
# 说明（R11-01，2026-10-26）：
#   ① **定位 = 非生产**。当前生产部署路径是 `deploy/start-backend.sh`（原生 uvicorn，见 docs/20
#      §一/§四/§八）；本镜像用于本地构建/预演。是否把镜像接进生产路径**待甲方裁定**——
#      裁定前不得据此镜像部署生产（生产 compose 里也没有后端服务）。
#   ② **时区钉死 `TZ=Asia/Shanghai`**（并安装 tzdata）：容器默认 UTC 会让业务日边界整体偏 8 小时
#      （打卡/到期/周月报/"今日"统计 —— A18-0022 红线）。与 `deploy/start-backend.sh` 同源。
#   ③ **禁止用 `DEBUG=true` 换可用性**：`config.py::validate_production` 的早退门是
#      `not is_production_env(APP_ENV) and DEBUG` —— 打开它会跳过全部生产红线
#      （固定验证码 1234 + mock 支付可结单），镜像因此在生产上必须保持 `DEBUG=false`。

# 2026-10-27 CI 实证：`python:3.13-slim` 已切到 Debian trixie，`libgdk-pixbuf2.0-0`
# 更名为 `libgdk-pixbuf-2.0-0` → apt 安装直接失败（新增的 image job 首跑红）。
# 钉 bookworm 保证包名与构建结果可复现；将来升 trixie 时同步改下面的包名。
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    TZ=Asia/Shanghai

# 运行期系统依赖：Pillow 海报渲染需要 pango/cairo/gdk-pixbuf + 中文字体；
# curl 供 HEALTHCHECK 使用；tzdata 必须显式装（slim 基础镜像不保证有 /usr/share/zoneinfo，
# 缺它时 `TZ` 环境变量会被静默忽略 → 仍按 UTC 走）。
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev \
    tzdata \
    fonts-noto-cjk fonts-wqy-microhei \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
# 先只拷依赖清单 → 业务代码改动不会让依赖层失效
COPY pyproject.toml uv.lock ./
# B8（2026-10-28）：依赖层先装**依赖**不装本包（--no-install-project）——此时 backend/
# 还没拷进来，装本包会得到空映射；COPY 之后再 sync 一次把本包装进 venv。
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8002/health || exit 1

CMD ["uv", "run", "--no-dev", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8002"]
