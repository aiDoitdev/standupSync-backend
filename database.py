import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

_raw_url = os.getenv("DATABASE_URL")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# asyncpg requires the postgresql+asyncpg:// scheme
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Supabase Supavisor runs in transaction-pooling mode, which does not support
# named prepared statements. Two layers must be disabled:
#
# 1. prepared_statement_cache_size=0 — turns off SQLAlchemy's own wrapper cache
#    so it calls asyncpg.prepare() with an empty name (unnamed = no server-side
#    persistence) for application queries.
#
# 2. statement_cache_size=0 — asyncpg's native cache size, passed through to
#    asyncpg.connect(). Without this, asyncpg still creates named prepared
#    statements (e.g. __asyncpg_stmt_3__) internally for type-codec introspection
#    on every new connection, which fails with DuplicatePreparedStatementError
#    when Supavisor hands back a recycled server connection.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
        "statement_cache_size": 0,
    },
)
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
Base = declarative_base()


async def get_db():
    """FastAPI dependency that yields a database session and ensures it's closed."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
