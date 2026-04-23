"""production billing upgrade — subscriptions, webhook_events, plan_expires_at

Revision ID: 0001
Revises:
Create Date: 2026-04-22

What this migration adds
------------------------
1. `subscriptions`   — full subscription lifecycle history (one row per event)
2. `webhook_events`  — idempotency store for Lemon Squeezy webhook deliveries
3. `teams.plan_expires_at` — UTC datetime; Starter access retained until this date
   after cancellation (grace period)

How to apply
------------
For an existing database (tables already exist from Base.metadata.create_all):

    # Mark the initial empty revision as applied so Alembic knows the baseline:
    alembic stamp 0001

    # Then on every future deploy just run:
    alembic upgrade head

For a fresh database:

    alembic upgrade head
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add grace-period column to teams ──────────────────────────────────
    op.add_column(
        "teams",
        sa.Column("plan_expires_at", sa.DateTime(), nullable=True),
    )

    # ── 2. subscriptions table ───────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("ls_subscription_id", sa.String(255), nullable=False),
        sa.Column("ls_customer_id", sa.String(255), nullable=True),
        sa.Column("ls_variant_id", sa.String(255), nullable=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("current_period_start", sa.DateTime(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_subscriptions_team_id", "subscriptions", ["team_id"])
    op.create_index("ix_subscriptions_ls_subscription_id", "subscriptions", ["ls_subscription_id"])

    # ── 3. webhook_events table ──────────────────────────────────────────────
    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("team_id", sa.String(255), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_webhook_events_event_id", "webhook_events", ["event_id"], unique=True)


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.drop_table("subscriptions")
    op.drop_column("teams", "plan_expires_at")
