"""
Centralised plan-limit constants and enforcement helpers.

All routers import from here so grace-period logic lives in exactly one place.
Billing is account-level: the manager User holds plan/plan_status/plan_expires_at.
Pass the manager User (not the Team) to these helpers.
"""
from datetime import datetime
from fastapi import HTTPException, status
from models import User

# ── Free plan hard limits ────────────────────────────────────────────────────
FREE_TEAM_LIMIT = 1
FREE_MEMBER_LIMIT = 1

# ── Starter plan limits (effectively unlimited) ──────────────────────────────
STARTER_MEMBER_LIMIT = None   # unlimited


def team_has_starter_access(manager: User) -> bool:
    """
    Return True if the team's manager currently has Starter-plan feature access.

    Grace period: a cancelled subscription keeps access until plan_expires_at
    so users aren't cut off mid-billing-period.
    """
    if manager.plan != "starter":
        return False
    if manager.plan_status == "active":
        return True
    # Past-due: keep access so we don't punish a temporary payment failure
    if manager.plan_status == "past_due":
        return True
    # Cancelled but within grace period
    if manager.plan_status == "canceled" and manager.plan_expires_at:
        return datetime.utcnow() < manager.plan_expires_at
    return False


def require_starter(manager: User, feature: str = "This feature") -> None:
    """Raise HTTP 402 if the manager does not have active Starter plan access."""
    if not team_has_starter_access(manager):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"{feature} is a Starter plan feature. "
                "Upgrade to Starter ($19/mo) to unlock it."
            ),
        )
