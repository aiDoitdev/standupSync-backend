"""
Migration: Add cost-intelligence columns to team_members table.

New columns:
  - hours_per_day  FLOAT    — confirmed working hours per day for this member
  - hours_confirmed BOOLEAN  — whether this member has confirmed their hours via check-in

Run once: python3 migrate_cost_intelligence.py
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
        await conn.execute(
            text("ALTER TABLE team_members ADD COLUMN IF NOT EXISTS hours_per_day FLOAT;")
        )
        await conn.execute(
            text(
                "ALTER TABLE team_members ADD COLUMN IF NOT EXISTS "
                "hours_confirmed BOOLEAN NOT NULL DEFAULT FALSE;"
            )
        )
    await engine.dispose()
    print("Migration complete: team_members.hours_per_day and team_members.hours_confirmed added.")


if __name__ == "__main__":
    asyncio.run(migrate())
