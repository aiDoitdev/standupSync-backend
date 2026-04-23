"""
Migration: add llm_response_json column to automation_analyses.

Stores the raw JSON blob returned by the LLM so subsequent fetches can read
from the database instead of re-calling the model.

Run:
  python3 migrate_llm_response.py
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from database import DATABASE_URL

_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
    },
)


async def main() -> None:
    async with _engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE automation_analyses "
            "ADD COLUMN IF NOT EXISTS llm_response_json TEXT;"
        ))
    print("✅  Migration complete: llm_response_json added to automation_analyses")
    await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
