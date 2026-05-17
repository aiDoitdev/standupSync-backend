"""Add canonical_kind column to team_questions

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-10

Adds an optional `canonical_kind` enum column on team_questions so the frontend
can map standup answers by intent (yesterday | today | wins | blockers | other)
instead of by display position. Existing rows are backfilled by order_index +
keyword heuristics, leaving NULL only for rows we can't confidently classify.
The frontend treats NULL as "use position fallback" so backfill is best-effort,
not authoritative.
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


_VALID_KINDS = ("yesterday", "today", "wins", "blockers", "other")


def upgrade() -> None:
    op.add_column(
        "team_questions",
        sa.Column("canonical_kind", sa.String(20), nullable=True),
    )
    op.create_check_constraint(
        "ck_team_questions_canonical_kind",
        "team_questions",
        f"canonical_kind IS NULL OR canonical_kind IN {_VALID_KINDS}",
    )

    # Best-effort backfill from existing label text.
    # Order matters: more-specific patterns first so 'blockers' wins over 'today'.
    op.execute(
        """
        UPDATE team_questions
        SET canonical_kind = 'blockers'
        WHERE canonical_kind IS NULL
          AND (is_blocker_type = TRUE OR LOWER(label) LIKE '%blocker%' OR LOWER(label) LIKE '%stuck%')
        """
    )
    op.execute(
        """
        UPDATE team_questions
        SET canonical_kind = 'wins'
        WHERE canonical_kind IS NULL
          AND (LOWER(label) LIKE '%win%' OR LOWER(label) LIKE '%shoutout%' OR LOWER(label) LIKE '%celebrate%')
        """
    )
    op.execute(
        """
        UPDATE team_questions
        SET canonical_kind = 'yesterday'
        WHERE canonical_kind IS NULL
          AND LOWER(label) LIKE '%yesterday%'
        """
    )
    op.execute(
        """
        UPDATE team_questions
        SET canonical_kind = 'today'
        WHERE canonical_kind IS NULL
          AND LOWER(label) LIKE '%today%'
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_team_questions_canonical_kind", "team_questions")
    op.drop_column("team_questions", "canonical_kind")
