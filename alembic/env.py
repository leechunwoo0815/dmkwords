from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Import all models so Alembic can detect them
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.database import Base  # noqa: E402

# Import domain models to register them with Base.metadata
# （新域模型落地时在此追加 import）
from backend.domain.activity.models import Activity, ActivityEnrollment  # noqa: E402, F401
from backend.domain.admin.models import AdminUser, AuditLog, SystemConfig  # noqa: E402, F401
from backend.domain.catalog.models import Book, BookCopy, QuizQuestion  # noqa: E402, F401
from backend.domain.circulation.models import BorrowRecord  # noqa: E402, F401
from backend.domain.reading.models import CheckIn, ReadingProgress, Reservation  # noqa: E402, F401
from backend.domain.billing.models import Deposit, DepositLedger  # noqa: E402, F401
from backend.domain.growth.models import (  # noqa: E402, F401
    CheckinStreakRecord,
    ChildGrowthState,
    MilestoneAward,
    PointLedger,
    QuizAttempt,
    WordsLedger,
)
from backend.domain.identity.models import Child, Order, Parent  # noqa: E402, F401

# this is the Alembic Config object
config = context.config

# DATABASE_URL env 显式优先（CI）；否则回落 backend 配置（.env，开发机）
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    from backend.config import get_settings  # noqa: E402

    db_url = get_settings().database_url
config.set_main_option("sqlalchemy.url", db_url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata to our Base
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
