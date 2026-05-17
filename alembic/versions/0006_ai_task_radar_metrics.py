"""Ai Task Radar metrics — per-task frequency/hours/cost + per-analysis rollup

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-16

Adds the columns that back ATR-2, ATR-4 and ATR-7:
  automation_tasks.mention_frequency / weekly_hours_saved / monthly_cost_saved_usd
  automation_analyses.weekly_hours_saved / monthly_cost_saved_usd / high_priority_task_count

ADD COLUMN IF NOT EXISTS is used because some environments provisioned these
tables via Base.metadata.create_all() rather than the migration chain.
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation_tasks ADD COLUMN IF NOT EXISTS mention_frequency INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE automation_tasks ADD COLUMN IF NOT EXISTS weekly_hours_saved DOUBLE PRECISION NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE automation_tasks ADD COLUMN IF NOT EXISTS monthly_cost_saved_usd DOUBLE PRECISION NOT NULL DEFAULT 0")

    op.execute("ALTER TABLE automation_analyses ADD COLUMN IF NOT EXISTS weekly_hours_saved DOUBLE PRECISION")
    op.execute("ALTER TABLE automation_analyses ADD COLUMN IF NOT EXISTS monthly_cost_saved_usd DOUBLE PRECISION")
    op.execute("ALTER TABLE automation_analyses ADD COLUMN IF NOT EXISTS high_priority_task_count INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE automation_tasks DROP COLUMN IF EXISTS mention_frequency")
    op.execute("ALTER TABLE automation_tasks DROP COLUMN IF EXISTS weekly_hours_saved")
    op.execute("ALTER TABLE automation_tasks DROP COLUMN IF EXISTS monthly_cost_saved_usd")

    op.execute("ALTER TABLE automation_analyses DROP COLUMN IF EXISTS weekly_hours_saved")
    op.execute("ALTER TABLE automation_analyses DROP COLUMN IF EXISTS monthly_cost_saved_usd")
    op.execute("ALTER TABLE automation_analyses DROP COLUMN IF EXISTS high_priority_task_count")
