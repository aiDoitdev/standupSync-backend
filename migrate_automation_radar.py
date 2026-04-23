"""
Migration: Create automation_analyses table for AI Automation Radar feature.

Stores per-team on-demand LLM analysis runs. One completed run allowed per team per week.

Run once: python3 migrate_automation_radar.py
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
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS automation_analyses (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                team_id      UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                created_by   UUID NOT NULL REFERENCES users(id),
                window_days  INTEGER NOT NULL DEFAULT 14,
                status       VARCHAR(20) NOT NULL DEFAULT 'completed',
                period_start DATE NOT NULL,
                period_end   DATE NOT NULL,
                findings_json TEXT,
                summary_text  TEXT,
                error_message TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_automation_analyses_team_id
                ON automation_analyses (team_id, created_at DESC);
        """))
    await engine.dispose()
    print("Migration complete: automation_analyses table created.")


if __name__ == "__main__":
    asyncio.run(migrate())
