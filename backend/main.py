# backend/main.py — FastAPI 入口
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.common.exceptions import BusinessException, business_exception_handler
from backend.config import get_settings, is_production_env
from backend.middleware.app_metrics import AppMetricsMiddleware
from backend.middleware.body_limit import BodySizeLimitMiddleware
from backend.middleware.media_cache import MediaCacheMiddleware
from backend.middleware.rate_limit import WriteRateLimitMiddleware
from backend.middleware.security_headers import SecurityHeadersMiddleware, apply_security_headers


class _RedactQueryString(logging.Filter):
    """访问日志抹掉 query string（第八轮审查 P2-16）。

    为什么必须有：媒体端点靠 `?token=` 鉴权（`<img>`/`<audio>` 带不了 Authorization 头），
    而 uvicorn 的访问日志会把**完整路径含 query** 打出来——`/api/miniapp/covers/3?token=<30天管理员凭证>`
    原样进日志文件。能读日志的人（运维/日志采集平台/误提交的日志附件）就能在 token 到期前
    **完整冒充家长或管理员**。nginx 侧同款处理见 `deploy/nginx/dmkwords.conf.template`
    的 `log_format dmkwords_noquery`（用 `$uri` 而非 `$request`）。

    做法是**脱敏而不是关日志**：`/path?token=xxx` → `/path?[REDACTED]`，路径与状态码照旧可查。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                a.split("?", 1)[0] + "?[REDACTED]"
                if isinstance(a, str) and a.startswith("/") and "?" in a
                else a
                for a in args
            )
        return True


logging.basicConfig(level=logging.INFO)
# uvicorn 的 access logger 由 uvicorn 自己配置（basicConfig 管不到），单独挂过滤器
logging.getLogger("uvicorn.access").addFilter(_RedactQueryString())
# 第九轮 P1-4：httpx（微信 code2session / access_token 走 URL query）是**另一条日志通道**，
# uvicorn.access 的过滤器管不到它——统一挂脱敏，见 `common/log_redaction`
from backend.common.log_redaction import install_log_redaction  # noqa: E402

install_log_redaction()
logger = logging.getLogger(__name__)

settings = get_settings()
settings.validate_production()


def _multi_process_sources(argv: list[str], env: dict[str, str]) -> list[str]:
    """找出所有"要求多进程"的入参（B2-7；复审 2026-11-26 补 `--workers=2` 等号形）。

    只认三种写法是不够的：`--workers 2`（分开）之外还有 `--workers=2`、`-w2`（粘连）、
    以及 gunicorn 风格的 `-w 2`。漏一种就等于守卫可以被一行参数绕过——复审实锤。
    返回空列表 = 单进程（或没提 worker）。
    """
    import re

    offenders: list[str] = []
    workers_env = (env.get("WEB_CONCURRENCY") or "").strip()
    if workers_env.isdigit() and int(workers_env) > 1:
        offenders.append(f"WEB_CONCURRENCY={workers_env}")

    glued = re.compile(r"^(?:--workers=|-w)(\d+)$")
    for i, arg in enumerate(argv):
        matched = glued.match(arg)
        if matched:
            if int(matched.group(1)) > 1:
                offenders.append(arg)
            continue
        if arg in ("--workers", "-w") and i + 1 < len(argv):
            value = argv[i + 1]
            if value.isdigit() and int(value) > 1:
                offenders.append(f"{arg} {value}")
    return offenders


def _assert_single_process() -> None:
    """B2-7（2026-11-26 双审查）：**多 worker 拒启**。

    限流计数（`middleware/rate_limit`）、应用指标（`metrics`）、live 采集缓存与调度器锁
    都是**进程内状态**：起 2 个 worker 等于把写接口限流阈值翻倍、把请求量/5xx 看板拆成两半、
    把 APScheduler 起两份（采样重复写）。这些退化是静默的（页面照常显示），所以必须在启动口
    硬拦，而不是写在文档里靠人记得。

    检测面：`WEB_CONCURRENCY`、`--workers N`、`--workers=N`、`-w N`、`-wN`（见
    `_multi_process_sources`，独立成函数是为了能直接用用例覆盖各种拼法）。
    """
    import os
    import sys

    offenders = _multi_process_sources(sys.argv, dict(os.environ))
    if offenders:
        raise RuntimeError(
            "本服务只允许单进程运行（限流/指标/live 缓存/调度器锁均为进程内状态）："
            f"检测到 {' / '.join(offenders)}。请去掉 --workers 与 WEB_CONCURRENCY>1 后重启；"
            "确需多进程须先改造成共享存储（Redis），见 docs/任务书-20261126-双审查合并整改.md B2-7。"
        )


_assert_single_process()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动：定时任务调度器（ADR-008 进程内 APScheduler；SCHEDULER_ENABLED=false 可关，F5）。
    事件订阅器在模块顶层注册（与 audit/growth 既有模式一致，TestClient 无 lifespan 也能跑 handler）。"""
    from backend.tasks.registry import start_scheduler, stop_scheduler

    if settings.SCHEDULER_ENABLED:
        start_scheduler()
    else:
        logger.info("APScheduler disabled by SCHEDULER_ENABLED=false")
    logger.info("lifespan startup complete")
    yield
    stop_scheduler()


def docs_endpoint_urls(app_env: str) -> dict[str, str | None]:
    """API 文档端点开关（用户拍板 a，2026-10-09）。

    生产恒关闭：/docs、/redoc、/openapi.json 是 FastAPI 内建端点，**不经过任何权限校验**
    （实测无 token 直打 200），等于把全部接口清单（含管理端）公开。契约的唯一来源是仓库快照
    `docs/api/openapi.json`（门禁 `export_openapi.py --check` 维护），开发/测试保持开启。

    第八轮审查 P2-15：判据从"字面等于 `production`"改为 `is_production_env`（**唯一来源**）
    ——`APP_ENV=prod` / 拼错 / 空串原先都判不出生产，接口清单直接公开。
    """
    if is_production_env(app_env):
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    **docs_endpoint_urls(settings.APP_ENV),
)

# 全局写限流（第七轮 P2-6）：**注册顺序有意为之**——`add_middleware` 是"后加者在外层"，
# 故本行写在 CORSMiddleware 之前 = 限流在 CORS **内侧**：429 响应仍带跨域头（否则浏览器
# 只报 CORS 错、家长看不到"请求过于频繁"的真实原因）。
app.add_middleware(WriteRateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # 来自 CORS_ORIGINS（.env）；空=同域部署不发跨域头
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(MediaCacheMiddleware)  # 媒体缓存头单一口径（docs/15 §22.5）

# 安全响应头（第八轮审查 P2-17）：**最后加 = 最外层**——限流 429、CORS 预检、业务异常
# 的响应同样带上 nosniff/frame-ancestors 等（错误页也一样会被塞进 iframe）。
# 与 media_cache 相反，这里是**覆盖**语义：安全头是下限，端点自己漏写也必须补上。
app.add_middleware(SecurityHeadersMiddleware)

# 请求体上限（第八轮审查 P2-19）：**在限流内侧、CORS 外侧**——先挡超大包再谈业务。
# 回环直连（无 nginx）与无凭证回调（/api/pay/**，必须 await request.body()）都靠它兜底。
app.add_middleware(BodySizeLimitMiddleware)
# FEAT-091：应用指标（请求量/4xx/5xx/P95）——**最后加 = 最外层**，最终状态码（含异常 500）都能记录；
# 只统计 `/api/*`，排除 `/health` 与 `/api/admin/system/*`（监控页自身轮询）。
app.add_middleware(AppMetricsMiddleware)
app.add_exception_handler(BusinessException, business_exception_handler)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未处理异常 → JSON 500，**并补上安全头**（第十轮 FE-4/W4-D3）。

    为什么必须有这个处理器：Starlette 的 `ServerErrorMiddleware` 位于**用户中间件外层**，
    未处理异常由它直接生成裸文本 500 → `SecurityHeadersMiddleware` 的补头代码不执行
    （实测 401/403/404/429 各 6/6 头，500 = 0/6）。安全头是**下限**（见该中间件 docstring
    "已有则覆盖"），错误页同样会被第三方 iframe 嵌入，不能只在正常路径兑现。
    体统一为 JSON，前端（管理端/小程序）的 `detail` 解析口径才一致。
    """
    response = JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
    apply_security_headers(request, response)
    return response


@app.get("/health")
def health() -> dict:
    """健康检查（容器探针用）。**恒 200**：进程活着就是 ok，不因依赖异常改状态码。

    第八轮审查 P1-11：附加 `scheduler` 只读状态（**不查库**）——定时任务整体停摆原先
    完全无声（`/health` 恒 200、看板只是 `last_run` 变旧）。字段是**加法**，探针与既有
    用例按原有字段判断不受影响。
    """
    from backend.tasks.registry import scheduler_state

    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "scheduler": scheduler_state(),
    }


#: 就绪判据：采样数据超过这个时长没更新 = 采样停摆（60 秒一采，5 分钟 = 连丢 5 次）
READY_SAMPLER_MAX_AGE_SECONDS = 300


def _ready_db_check() -> tuple[bool, str]:
    """`SELECT 1`（唯一能证明"库连得上"的最小代价探针）。"""
    from sqlalchemy import text

    from backend.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001 - 探针必须把任何失败翻成"未就绪"，不能抛
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return True, ""


def _ready_sampler_check() -> tuple[bool, str, float | None]:
    """采样新鲜度：`system_metrics` 最近一行的年龄（B2-1 的"关键任务新鲜度"）。"""
    from datetime import datetime

    from sqlalchemy import text

    from backend.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            latest = db.execute(text("SELECT MAX(collected_at) FROM system_metrics")).scalar()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:120]}", None
    if latest is None:
        return False, "system_metrics 无数据（采样从未成功）", None
    if isinstance(latest, str):  # 驱动返回字符串时兜底解析
        try:
            from datetime import datetime as _dt

            latest = _dt.fromisoformat(latest)
        except ValueError:
            return False, f"collected_at 解析失败: {latest!r}", None
    age = (datetime.now() - latest).total_seconds()
    if age > READY_SAMPLER_MAX_AGE_SECONDS:
        return (
            False,
            f"最近一次采样 {int(age)} 秒前（阈值 {READY_SAMPLER_MAX_AGE_SECONDS} 秒）",
            age,
        )
    return True, "", age


@app.get("/ready")
def ready() -> JSONResponse:
    """就绪探针（B2-1，2026-11-26 双审查）：**能不能受理业务**，与 `/health`（进程活着）分开。

    为什么必须分开：`/health` 恒 200 的语义是对的（进程活着），但它挡不住"进程活着、库连不上、
    定时任务全停"这种**半死**状态——外部探针只看 `/health` 会一直绿灯，直到用户报错。
    判据三项，任一不过 → **503**，响应体写明哪项不过（探针抓 JSON 就能看到原因）：

    1. `db`：`SELECT 1`；
    2. `scheduler`：调度器在跑且握着锁连接（`SCHEDULER_ENABLED=false` 时不作判据，仅回显）；
    3. `sampler`：`system_metrics` 最近一行不超过 5 分钟（同样仅在调度器启用时判）。
    """
    from backend.tasks.registry import scheduler_state

    checks: dict[str, dict] = {}
    ok = True

    db_ok, db_error = _ready_db_check()
    checks["db"] = {"ok": db_ok, **({"error": db_error} if db_error else {})}
    ok = ok and db_ok

    sched = scheduler_state()
    checks["scheduler"] = {"ok": True, **sched}
    if sched.get("enabled"):
        sched_ok = bool(sched.get("running")) and bool(sched.get("has_lock_conn"))
        checks["scheduler"]["ok"] = sched_ok
        if not sched_ok:
            checks["scheduler"]["error"] = "调度器未运行或未持有锁连接（定时任务不会执行）"
            ok = False

        if db_ok:  # 库连不上时再去查 MAX() 只会重复报错
            fresh_ok, fresh_error, age = _ready_sampler_check()
            checks["sampler"] = {
                "ok": fresh_ok,
                **({"age_seconds": round(age, 1)} if age is not None else {}),
                **({"error": fresh_error} if fresh_error else {}),
            }
            ok = ok and fresh_ok
        else:
            checks["sampler"] = {"ok": False, "error": "数据库不可用，无法判定采样新鲜度"}
            ok = False
    else:
        checks["sampler"] = {"ok": True, "skipped": "SCHEDULER_ENABLED=false（本进程不采样）"}

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


from backend.domain.activity.miniapp_router import router as activity_miniapp_router  # noqa: E402
from backend.domain.activity.router import router as activity_router  # noqa: E402
from backend.domain.admin.audit_handlers import register_audit_handlers  # noqa: E402
from backend.domain.admin.router import router as admin_router  # noqa: E402
from backend.domain.billing.miniapp_router import router as billing_miniapp_router  # noqa: E402
from backend.domain.billing.router import router as billing_router  # noqa: E402
from backend.domain.catalog.router import router as catalog_router  # noqa: E402
from backend.domain.circulation.router import router as circulation_router  # noqa: E402
from backend.domain.growth.growth_handlers import register_growth_handlers  # noqa: E402
from backend.domain.growth.miniapp_router import router as growth_miniapp_router  # noqa: E402
from backend.domain.growth.router import router as growth_router  # noqa: E402
from backend.domain.identity.miniapp_router import router as identity_miniapp_router  # noqa: E402
from backend.domain.identity.pay_notify_router import router as pay_notify_router  # noqa: E402
from backend.domain.identity.router import router as identity_router  # noqa: E402
from backend.domain.reading.miniapp_router import router as miniapp_router  # noqa: E402
from backend.domain.reading.router import router as reading_router  # noqa: E402
from backend.domain.reading_circle.miniapp_router import (  # noqa: E402
    router as reading_circle_miniapp_router,
)
from backend.domain.reading_circle.router import router as reading_circle_router  # noqa: E402
from backend.tasks.notify_handlers import register_notification_handlers  # noqa: E402

register_audit_handlers()
register_growth_handlers()
register_notification_handlers()

app.include_router(admin_router, prefix="/api/admin")
app.include_router(catalog_router, prefix="/api/admin")
app.include_router(circulation_router, prefix="/api/admin")
app.include_router(identity_router, prefix="/api/admin")
app.include_router(billing_router, prefix="/api/admin")
app.include_router(miniapp_router, prefix="/api/miniapp")
app.include_router(activity_router, prefix="/api/admin")
app.include_router(activity_miniapp_router, prefix="/api/miniapp")
app.include_router(identity_miniapp_router, prefix="/api/miniapp")
app.include_router(billing_miniapp_router, prefix="/api/miniapp")
app.include_router(growth_router, prefix="/api/admin")
app.include_router(growth_miniapp_router, prefix="/api/miniapp")
app.include_router(reading_router, prefix="/api/admin")
app.include_router(reading_circle_router, prefix="/api/admin")
app.include_router(reading_circle_miniapp_router, prefix="/api/miniapp")
# 微信支付回调（WM12-A）：无登录态、凭平台证书验签；微信要求 https 正式域名可达
app.include_router(pay_notify_router, prefix="/api/pay")
