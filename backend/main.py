# backend/main.py — FastAPI 入口
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.common.exceptions import BusinessException, business_exception_handler
from backend.config import get_settings, is_production_env
from backend.middleware.body_limit import BodySizeLimitMiddleware
from backend.middleware.media_cache import MediaCacheMiddleware
from backend.middleware.rate_limit import WriteRateLimitMiddleware
from backend.middleware.security_headers import SecurityHeadersMiddleware


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
logger = logging.getLogger(__name__)

settings = get_settings()
settings.validate_production()


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
app.add_exception_handler(BusinessException, business_exception_handler)


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
