from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from app.core.config import get_settings

_settings = get_settings()

# Supabase PgBouncer (transaction mode) rejects prepared statements —
# disable the asyncpg internal cache and force unnamed execution.
# json_serializer/deserializer=None prevents the asyncpg dialect from running
# a prepared-statement codec introspection on each new connection.
engine = create_async_engine(
    _settings.database_url_async,
    echo=False,
    json_serializer=None,
    json_deserializer=None,
    disable_prepared_statement_cache=True,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
    },
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_db_health() -> bool:
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
