# backend/database.py — MySQL-only 会话与引擎（宪法：单一环境）
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    """全局 ORM 声明基类（所有 Model 继承）。"""


engine = create_engine(
    settings.database_url,
    isolation_level="READ COMMITTED",  # E-00（外部审计 20260903）：RR 下锁内 COUNT/SUM 守卫读旧快照，并发失效；RC 每次语句读最新已提交
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session]:
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session() -> Session:
    """非请求上下文（定时任务/后台执行）显式会话，调用方负责 close。"""
    return SessionLocal()
