"""Add compound indexes, UniqueConstraints, CHECK constraints, and JSONB columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── UniqueConstraints ──────────────────────────────────────────────────────
    op.create_unique_constraint(
        "uq_team_members_team_user", "team_members", ["team_id", "user_id"]
    )
    op.create_unique_constraint(
        "uq_checkins_team_user_date", "checkins", ["team_id", "user_id", "date"]
    )
    op.create_unique_constraint(
        "uq_checkin_answers_checkin_question", "checkin_answers", ["checkin_id", "question_id"]
    )
    op.create_unique_constraint(
        "uq_automation_analyses_team_period", "automation_analyses", ["team_id", "period_start"]
    )
    op.create_unique_constraint(
        "uq_automation_integrations_team_provider", "automation_integrations", ["team_id", "provider"]
    )

    # ── CHECK constraints ──────────────────────────────────────────────────────
    op.create_check_constraint("ck_users_role", "users", "role IN ('manager', 'member')")
    op.create_check_constraint("ck_teams_plan", "teams", "plan IN ('free', 'starter')")
    op.create_check_constraint("ck_teams_plan_status", "teams", "plan_status IN ('active', 'past_due', 'canceled')")
    op.create_check_constraint("ck_team_members_status", "team_members", "status IN ('pending', 'active', 'inactive')")
    op.create_check_constraint("ck_team_members_role", "team_members", "role IN ('member', 'co-manager')")
    op.create_check_constraint("ck_blockers_status", "blockers", "status IN ('open', 'acknowledged', 'in_progress', 'resolved')")
    op.create_check_constraint("ck_subscriptions_plan", "subscriptions", "plan IN ('free', 'starter')")
    op.create_check_constraint("ck_automation_analyses_status", "automation_analyses", "status IN ('completed', 'failed')")
    op.create_check_constraint("ck_automation_analyses_trigger", "automation_analyses", "trigger IN ('scheduled', 'manual_admin', 'initial')")
    op.create_check_constraint("ck_automation_tasks_tier", "automation_tasks", "tier IN ('P1', 'P2', 'P3')")
    op.create_check_constraint("ck_automation_tasks_score", "automation_tasks", "automation_score >= 0 AND automation_score <= 100")
    op.create_check_constraint("ck_automation_schedules_cadence", "automation_schedules", "cadence IN ('weekly', 'biweekly', 'monthly')")

    # ── Compound indexes ───────────────────────────────────────────────────────
    op.create_index("ix_checkins_team_date",            "checkins",           ["team_id", "date"])
    op.create_index("ix_checkins_user_date",            "checkins",           ["user_id", "date"])
    op.create_index("ix_team_members_team_status",      "team_members",       ["team_id", "status"])
    op.create_index("ix_team_members_user_id",          "team_members",       ["user_id"])
    op.create_index("ix_blockers_team_status",          "blockers",           ["team_id", "status"])
    op.create_index("ix_blockers_user_id",              "blockers",           ["user_id"])
    op.create_index("ix_blockers_assigned_to",          "blockers",           ["assigned_to"])
    op.create_index("ix_automation_analyses_team_id",   "automation_analyses", ["team_id"])
    op.create_index("ix_automation_analyses_team_created", "automation_analyses", ["team_id", "created_at"])
    op.create_index("ix_automation_tasks_analysis_id",  "automation_tasks",   ["analysis_id"])
    op.create_index("ix_automation_integrations_team_id", "automation_integrations", ["team_id"])
    op.create_index("ix_blocker_comments_blocker_id",   "blocker_comments",   ["blocker_id"])
    op.create_index("ix_blocker_resolutions_blocker_id","blocker_resolutions",["blocker_id"])
    op.create_index("ix_checkin_answers_checkin_id",    "checkin_answers",    ["checkin_id"])
    op.create_index("ix_subscriptions_team_id",         "subscriptions",      ["team_id"])
    op.create_index("ix_subscriptions_ls_sub_id",       "subscriptions",      ["ls_subscription_id"])
    op.create_index("ix_team_questions_team_order",     "team_questions",     ["team_id", "order_index"])
    op.create_index("ix_invites_team_email",            "invites",            ["team_id", "email"])
    op.create_index("ix_otp_email_used",                "otp_verifications",  ["email", "used"])

    # ── JSONB column upgrades ──────────────────────────────────────────────────
    # findings_json: TEXT → JSONB
    op.execute("ALTER TABLE automation_analyses ALTER COLUMN findings_json TYPE JSONB USING findings_json::jsonb")
    # llm_response_json: TEXT → JSONB
    op.execute("ALTER TABLE automation_analyses ALTER COLUMN llm_response_json TYPE JSONB USING llm_response_json::jsonb")
    # suggested_tools_json: TEXT → JSONB
    op.execute("ALTER TABLE automation_tasks ALTER COLUMN suggested_tools_json TYPE JSONB USING suggested_tools_json::jsonb")

    # ── updated_at on team_members ─────────────────────────────────────────────
    op.add_column("team_members", sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.func.now()))

    # ── Fix webhook_events.team_id from String to UUID FK ─────────────────────
    op.execute("""
        ALTER TABLE webhook_events
        ALTER COLUMN team_id TYPE UUID
        USING CASE WHEN team_id ~ '^[0-9a-f-]{36}$' THEN team_id::uuid ELSE NULL END
    """)


def downgrade() -> None:
    op.drop_index("ix_otp_email_used", table_name="otp_verifications")
    op.drop_index("ix_invites_team_email", table_name="invites")
    op.drop_index("ix_team_questions_team_order", table_name="team_questions")
    op.drop_index("ix_subscriptions_ls_sub_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_team_id", table_name="subscriptions")
    op.drop_index("ix_checkin_answers_checkin_id", table_name="checkin_answers")
    op.drop_index("ix_blocker_resolutions_blocker_id", table_name="blocker_resolutions")
    op.drop_index("ix_blocker_comments_blocker_id", table_name="blocker_comments")
    op.drop_index("ix_automation_integrations_team_id", table_name="automation_integrations")
    op.drop_index("ix_automation_tasks_analysis_id", table_name="automation_tasks")
    op.drop_index("ix_automation_analyses_team_created", table_name="automation_analyses")
    op.drop_index("ix_automation_analyses_team_id", table_name="automation_analyses")
    op.drop_index("ix_blockers_assigned_to", table_name="blockers")
    op.drop_index("ix_blockers_user_id", table_name="blockers")
    op.drop_index("ix_blockers_team_status", table_name="blockers")
    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_index("ix_team_members_team_status", table_name="team_members")
    op.drop_index("ix_checkins_user_date", table_name="checkins")
    op.drop_index("ix_checkins_team_date", table_name="checkins")

    op.drop_constraint("uq_automation_integrations_team_provider", "automation_integrations")
    op.drop_constraint("uq_automation_analyses_team_period", "automation_analyses")
    op.drop_constraint("uq_checkin_answers_checkin_question", "checkin_answers")
    op.drop_constraint("uq_checkins_team_user_date", "checkins")
    op.drop_constraint("uq_team_members_team_user", "team_members")

    op.drop_column("team_members", "updated_at")
