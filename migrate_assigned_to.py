"""
Migration: Add assigned_to column to blockers table.

Run once:  python3 migrate_assigned_to.py
Safe to run multiple times — uses IF NOT EXISTS.
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from database import DATABASE_URL


async def migrate():
    engine = create_async_engine(
        DATABASE_URL,
        echo=True,
        connect_args={
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: "",
        },
    )

    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE blockers ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES users(id) ON DELETE SET NULL;"
        ))
        print("✓ blockers.assigned_to column ensured")

    await engine.dispose()
    print("Migration complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
