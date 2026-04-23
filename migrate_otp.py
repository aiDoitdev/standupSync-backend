"""
Migration: Add otp_verifications table for email-based signup verification.

Run once:  python3 migrate_otp.py
Safe to run multiple times — uses CREATE TABLE IF NOT EXISTS.
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
            CREATE TABLE IF NOT EXISTS otp_verifications (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) NOT NULL,
                otp_code VARCHAR(10) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_otp_verifications_email
            ON otp_verifications (email);
        """))
        print("✓ otp_verifications table and index ensured")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
