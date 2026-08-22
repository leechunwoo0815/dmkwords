# backend/main.py — FastAPI 入口（骨架版）
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
settings.validate_production()

app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 骨架期放开；上线前收紧为小程序/管理端域名
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


# 域 Router 在各域就绪后按序挂载（F0 起逐域启用）：
# from backend.domain.admin.router import router as admin_router  # noqa: ERA001
# app.include_router(admin_router, prefix="/api/admin")
