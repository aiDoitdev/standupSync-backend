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

# Supabase uses PgBouncer in transaction mode. SQLAlchemy's asyncpg adapter
# still prepares statements during connection setup, so disable the internal
# asyncpg prepared statement cache and force unnamed statement execution.
#
# json_serializer/json_deserializer=None prevents SA's asyncpg dialect from
# installing the jsonb codec on every new connection (the codec setup runs
# prepared-statement introspection under the hood, which pgbouncer rejects).
# The project stores JSON as TEXT with explicit json.dumps / json.loads, so
# we don't need the dialect-level JSON plumbing.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    json_serializer=None,
    json_deserializer=None,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
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
