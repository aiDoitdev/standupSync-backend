import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure the backend root is on sys.path so both old and new imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import Base + all models so autogenerate sees every table.
# New package takes precedence; fall back to legacy flat layout if not present.
try:
    from app.models.base import Base  # noqa: F401
    import app.models  # noqa: F401
except ImportError:
    from database import Base  # noqa: F401
    import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    raw = os.getenv("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


def run_migrations_offline() -> None:
    """Emit SQL to stdout — no live DB connection required."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect to the DB and run pending migrations."""
    connectable = create_async_engine(
        _get_url(),
        connect_args={"prepared_statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
