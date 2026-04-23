"""
Centralised plan-limit constants and enforcement helpers.

All routers import from here so grace-period logic lives in exactly one place.
"""
from datetime import datetime
from fastapi import HTTPException, status
from models import Team

# ── Free plan hard limits ────────────────────────────────────────────────────
FREE_TEAM_LIMIT = 1
FREE_MEMBER_LIMIT = 5

# ── Starter plan limits (effectively unlimited) ──────────────────────────────
STARTER_MEMBER_LIMIT = None   # unlimited


def team_has_starter_access(team: Team) -> bool:
    """
    Return True if the team currently has Starter-plan feature access.

    Grace period: a cancelled subscription keeps access until plan_expires_at
    so users aren't cut off mid-billing-period.
    """
    if team.plan != "starter":
        return False
    if team.plan_status == "active":
        return True
    # Past-due: keep access so we don't punish a temporary payment failure
    if team.plan_status == "past_due":
        return True
    # Cancelled but within grace period
    if team.plan_status == "canceled" and team.plan_expires_at:
        return datetime.utcnow() < team.plan_expires_at
    return False


def require_starter(team: Team, feature: str = "This feature") -> None:
    """Raise HTTP 402 if the team does not have active Starter plan access."""
    if not team_has_starter_access(team):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"{feature} is a Starter plan feature. "
                "Upgrade to Starter ($19/mo) to unlock it."
            ),
        )
