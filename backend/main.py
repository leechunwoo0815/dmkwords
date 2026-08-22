# backend/main.py — FastAPI 入口
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.common.exceptions import BusinessException, business_exception_handler
from backend.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
settings.validate_production()

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

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


from backend.domain.admin.audit_handlers import register_audit_handlers  # noqa: E402
from backend.domain.admin.router import router as admin_router  # noqa: E402
from backend.domain.billing.router import router as billing_router  # noqa: E402
from backend.domain.catalog.router import router as catalog_router  # noqa: E402
from backend.domain.identity.router import router as identity_router  # noqa: E402

register_audit_handlers()

app.include_router(admin_router, prefix="/api/admin")
app.include_router(catalog_router, prefix="/api/admin")
app.include_router(identity_router, prefix="/api/admin")
app.include_router(billing_router, prefix="/api/admin")
