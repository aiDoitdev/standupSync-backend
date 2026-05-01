"""Migrate billing from per-team to per-user (account-level subscriptions)

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-22

What this migration does
------------------------
1. Adds plan/billing fields to `users` — one subscription covers all teams
   owned by that manager.
2. Drops plan/billing fields from `teams` — plan is now derived from the
   owning manager's user record.
3. Migrates `subscriptions.team_id`    → `subscriptions.user_id`
4. Migrates `webhook_events.team_id`   → `webhook_events.user_id`

How to apply
------------
    alembic upgrade head

Downgrade rolls back all changes (plan columns restored to teams, user
columns dropped, subscriptions/webhook_events reverted).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add billing fields to users ──────────────────────────────────────
    op.add_column("users", sa.Column("plan", sa.String(20), nullable=False, server_default="free"))
    op.add_column("users", sa.Column("plan_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("users", sa.Column("plan_expires_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("ls_customer_id", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("ls_subscription_id", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("ls_variant_id", sa.String(255), nullable=True))
    op.create_check_constraint("ck_users_plan", "users", "plan IN ('free', 'starter')")
    op.create_check_constraint("ck_users_plan_status", "users", "plan_status IN ('active', 'past_due', 'canceled')")
    op.create_index("ix_users_ls_subscription_id", "users", ["ls_subscription_id"])

    # ── 2. Migrate subscriptions: team_id → user_id ──────────────────────────
    # Use CASCADE to silently drop the unnamed FK and any dependent objects.
    # The subscriptions table is empty at this point (no real payments yet),
    # so no data migration is required.
    op.execute("DROP INDEX IF EXISTS ix_subscriptions_team_id")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS team_id CASCADE")
    op.add_column(
        "subscriptions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000000",
        ),
    )
    op.alter_column("subscriptions", "user_id", server_default=None)
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    # ── 3. Migrate webhook_events: team_id → user_id ──────────────────────────
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS team_id CASCADE")
    op.add_column(
        "webhook_events",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_webhook_events_user_id", "webhook_events", ["user_id"])

    # ── 4. Drop billing fields from teams ─────────────────────────────────────
    # Drop CHECK constraints added in migration 0002 before dropping columns.
    # Use IF EXISTS to handle partial migrations where 0002 was stamped but not fully applied.
    op.execute("ALTER TABLE teams DROP CONSTRAINT IF EXISTS ck_teams_plan")
    op.execute("ALTER TABLE teams DROP CONSTRAINT IF EXISTS ck_teams_plan_status")
    op.drop_column("teams", "plan", if_exists=True)
    op.drop_column("teams", "plan_status", if_exists=True)
    op.drop_column("teams", "plan_expires_at", if_exists=True)
    op.drop_column("teams", "ls_customer_id", if_exists=True)
    op.drop_column("teams", "ls_subscription_id", if_exists=True)
    op.drop_column("teams", "ls_variant_id", if_exists=True)


def downgrade() -> None:
    # ── Restore billing fields to teams ──────────────────────────────────────
    op.add_column("teams", sa.Column("plan", sa.String(20), nullable=False, server_default="free"))
    op.add_column("teams", sa.Column("plan_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("teams", sa.Column("plan_expires_at", sa.DateTime(), nullable=True))
    op.add_column("teams", sa.Column("ls_customer_id", sa.String(255), nullable=True))
    op.add_column("teams", sa.Column("ls_subscription_id", sa.String(255), nullable=True))
    op.add_column("teams", sa.Column("ls_variant_id", sa.String(255), nullable=True))
    op.create_check_constraint("ck_teams_plan", "teams", "plan IN ('free', 'starter')")
    op.create_check_constraint("ck_teams_plan_status", "teams", "plan_status IN ('active', 'past_due', 'canceled')")

    # ── Restore webhook_events.team_id ────────────────────────────────────────
    op.drop_index("ix_webhook_events_user_id", table_name="webhook_events")
    op.execute("ALTER TABLE webhook_events DROP COLUMN IF EXISTS user_id CASCADE")
    op.add_column(
        "webhook_events",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── Restore subscriptions.team_id ─────────────────────────────────────────
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS user_id CASCADE")
    op.add_column(
        "subscriptions",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
            server_default="'00000000-0000-0000-0000-000000000000'",
        ),
    )
    op.alter_column("subscriptions", "team_id", server_default=None)
    op.create_index("ix_subscriptions_team_id", "subscriptions", ["team_id"])

    # ── Drop billing fields from users ────────────────────────────────────────
    op.drop_index("ix_users_ls_subscription_id", table_name="users")
    op.drop_constraint("ck_users_plan_status", "users", type_="check")
    op.drop_constraint("ck_users_plan", "users", type_="check")
    op.drop_column("users", "ls_variant_id")
    op.drop_column("users", "ls_subscription_id")
    op.drop_column("users", "ls_customer_id")
    op.drop_column("users", "plan_expires_at")
    op.drop_column("users", "plan_status")
    op.drop_column("users", "plan")
