# backend/main.py — FastAPI 入口
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.common.exceptions import BusinessException, business_exception_handler
from backend.config import get_settings

logging.basicConfig(level=logging.INFO)
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


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # admin-web 开发端口；上线前收紧为正式域名
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(BusinessException, business_exception_handler)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


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
from backend.domain.identity.router import router as identity_router  # noqa: E402
from backend.domain.reading.miniapp_router import router as miniapp_router  # noqa: E402
from backend.domain.reading.router import router as reading_router  # noqa: E402
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
