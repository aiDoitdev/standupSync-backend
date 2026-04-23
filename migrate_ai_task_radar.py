"""
Migration: Ai Task Radar — weekly-scheduled, team/member/task-level AI automation analysis.

Adds:
  - automation_schedules          : per-team cadence/day/time/timezone config + pre-computed next_run_at
  - automation_tasks              : normalized per-task LLM output (one row per inferred task)
  - automation_integrations       : stub for future Jira/Linear/Notion integrations
  - New columns on automation_analyses:
        trigger VARCHAR(20), team_score INTEGER, member_count INTEGER,
        task_count INTEGER, is_empty BOOLEAN

The scheduler itself guarantees at-most-once firing by advancing next_run_at
immediately after each due run (regardless of success), so no DB-level UNIQUE
constraint is required here (and avoiding one lets admin/manual runs coexist
with scheduled runs on the same day).

Idempotent: uses IF NOT EXISTS everywhere. Safe to re-run.

Run once: python3 migrate_ai_task_radar.py
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
        # --- Extend automation_analyses --------------------------------------
        await conn.execute(text("""
            ALTER TABLE automation_analyses
                ADD COLUMN IF NOT EXISTS trigger      VARCHAR(20) NOT NULL DEFAULT 'manual_admin',
                ADD COLUMN IF NOT EXISTS team_score   INTEGER,
                ADD COLUMN IF NOT EXISTS member_count INTEGER,
                ADD COLUMN IF NOT EXISTS task_count   INTEGER,
                ADD COLUMN IF NOT EXISTS is_empty     BOOLEAN NOT NULL DEFAULT FALSE;
        """))

        # --- automation_schedules --------------------------------------------
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS automation_schedules (
                team_id        UUID PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
                cadence        VARCHAR(20) NOT NULL DEFAULT 'weekly',   -- weekly | biweekly | monthly
                day_of_week    INTEGER NOT NULL DEFAULT 0,              -- 0=Mon ... 6=Sun
                week_of_month  INTEGER,                                  -- 1..4 for monthly; NULL otherwise
                run_time       VARCHAR(5) NOT NULL DEFAULT '08:00',      -- HH:MM in timezone
                timezone       VARCHAR(100) NOT NULL DEFAULT 'Asia/Kolkata',
                enabled        BOOLEAN NOT NULL DEFAULT TRUE,
                next_run_at    TIMESTAMPTZ,                              -- pre-computed, UTC
                last_run_at    TIMESTAMPTZ,
                failure_count  INTEGER NOT NULL DEFAULT 0,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_automation_schedules_next_run_at
                ON automation_schedules (next_run_at)
                WHERE enabled = TRUE;
        """))

        # --- automation_tasks -------------------------------------------------
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS automation_tasks (
                id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                analysis_id           UUID NOT NULL REFERENCES automation_analyses(id) ON DELETE CASCADE,
                user_id               UUID REFERENCES users(id) ON DELETE SET NULL,
                assigned_name         VARCHAR(255),                     -- echoed back from LLM (fallback when user_id can't be mapped)
                task_title            VARCHAR(500) NOT NULL,
                task_description      TEXT,
                automation_score      INTEGER NOT NULL DEFAULT 0,       -- 0..100
                tier                  VARCHAR(4) NOT NULL DEFAULT 'P3', -- P1 | P2 | P3
                suggested_tools_json  JSONB NOT NULL DEFAULT '[]',      -- [{name, prompt}]
                suggested_workflow    TEXT,
                general_suggestion    TEXT,
                source                VARCHAR(32) NOT NULL DEFAULT 'checkin',  -- checkin | integration_jira | integration_linear | integration_notion
                source_ref            VARCHAR(255),
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_automation_tasks_analysis_id
                ON automation_tasks (analysis_id);
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_automation_tasks_analysis_user
                ON automation_tasks (analysis_id, user_id);
        """))

        # --- automation_integrations (stub) ----------------------------------
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS automation_integrations (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                team_id      UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                provider     VARCHAR(20) NOT NULL,                    -- jira | linear | notion
                status       VARCHAR(20) NOT NULL DEFAULT 'disconnected',
                config_json  TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (team_id, provider)
            );
        """))

        # --- Normalise JSONB columns to TEXT ---------------------------------
        # Earlier migration runs may have created these columns as JSONB. Supabase
        # pools connections through pgbouncer in transaction mode, which trips
        # asyncpg's jsonb codec setup. Keep the DB columns plain TEXT so the ORM
        # round-trips JSON via json.dumps / json.loads — same pattern the legacy
        # `findings_json` column uses.
        await conn.execute(text("""
            ALTER TABLE automation_tasks
                ALTER COLUMN suggested_tools_json DROP DEFAULT,
                ALTER COLUMN suggested_tools_json TYPE TEXT USING suggested_tools_json::text,
                ALTER COLUMN suggested_tools_json SET DEFAULT '[]';
        """))
        await conn.execute(text("""
            ALTER TABLE automation_integrations
                ALTER COLUMN config_json TYPE TEXT USING config_json::text;
        """))
    await engine.dispose()
    print("Migration complete: Ai Task Radar tables + columns created.")


if __name__ == "__main__":
    asyncio.run(migrate())
