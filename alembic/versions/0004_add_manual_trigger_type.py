"""Add 'manual' to automation_analyses trigger check constraint

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-09
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF EXISTS — some environments provisioned automation_analyses via
    # Base.metadata.create_all() which never materialized this CHECK constraint.
    op.execute("ALTER TABLE automation_analyses DROP CONSTRAINT IF EXISTS ck_automation_analyses_trigger")
    op.create_check_constraint(
        "ck_automation_analyses_trigger",
        "automation_analyses",
        "trigger IN ('scheduled', 'manual_admin', 'initial', 'manual')",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE automation_analyses DROP CONSTRAINT IF EXISTS ck_automation_analyses_trigger")
    op.create_check_constraint(
        "ck_automation_analyses_trigger",
        "automation_analyses",
        "trigger IN ('scheduled', 'manual_admin', 'initial')",
    )
