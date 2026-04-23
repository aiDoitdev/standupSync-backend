"""
Migration v2: Add per-member timezone/send_time/currency, TeamQuestion table, CheckinAnswer table.

Run once:  python3 migrate_v2.py
Safe to run multiple times — all changes use IF NOT EXISTS / DO NOTHING.
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
        # ── Issue 1 & 2: per-member timezone, send_time, currency ──────────────
        await conn.execute(text(
            "ALTER TABLE team_members ADD COLUMN IF NOT EXISTS timezone VARCHAR(100) DEFAULT 'Asia/Kolkata';"
        ))
        await conn.execute(text(
            "ALTER TABLE team_members ADD COLUMN IF NOT EXISTS send_time VARCHAR(5) DEFAULT '09:00';"
        ))
        await conn.execute(text(
            "ALTER TABLE team_members ADD COLUMN IF NOT EXISTS currency VARCHAR(10) DEFAULT 'INR';"
        ))
        print("✓ team_members: timezone, send_time, currency columns ensured")

        # ── Issue 4: configurable questions table ───────────────────────────────
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS team_questions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                order_index INTEGER NOT NULL DEFAULT 0,
                label VARCHAR(500) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                is_blocker_type BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        print("✓ team_questions table ensured")

        # ── Issue 4: checkin answers table ──────────────────────────────────────
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS checkin_answers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                checkin_id UUID NOT NULL REFERENCES checkins(id) ON DELETE CASCADE,
                question_id UUID NOT NULL REFERENCES team_questions(id) ON DELETE CASCADE,
                answer TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        print("✓ checkin_answers table ensured")

        # ── Seed TeamQuestion rows for existing teams that don't have any ───────
        # (The application also does this lazily on first access, but running it
        #  upfront ensures the scheduler can work without first HTTP request.)
        await conn.execute(text("""
            INSERT INTO team_questions (team_id, order_index, label, enabled, is_blocker_type)
            SELECT
                t.id,
                q.ord,
                q.lbl,
                TRUE,
                q.is_blocker
            FROM teams t
            CROSS JOIN (VALUES
                (0, 'What did you accomplish yesterday?', FALSE),
                (1, 'What will you work on today?',      FALSE),
                (2, 'Any blockers or issues?',            TRUE)
            ) AS q(ord, lbl, is_blocker)
            WHERE NOT EXISTS (
                SELECT 1 FROM team_questions tq WHERE tq.team_id = t.id
            )
            ON CONFLICT DO NOTHING;
        """))
        print("✓ Seeded default questions for existing teams (skipped if already present)")

    await engine.dispose()
    print("\nMigration v2 complete.")


if __name__ == "__main__":
    asyncio.run(migrate())
