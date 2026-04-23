from datetime import datetime, timezone
from fastapi import HTTPException, status

FREE_TEAM_LIMIT = 1
FREE_MEMBER_LIMIT = 1      # manager + 1 invited member = 2 total on free plan
STARTER_MEMBER_LIMIT = None


def user_has_starter_access(user) -> bool:
    """Check if a user's account-level subscription grants Starter access."""
    if user is None or user.plan != "starter":
        return False
    if user.plan_status == "active":
        return True
    if user.plan_status == "past_due":
        return True
    if user.plan_status == "canceled" and user.plan_expires_at:
        return datetime.now(timezone.utc).replace(tzinfo=None) < user.plan_expires_at
    return False


def require_starter(user, feature: str = "This feature") -> None:
    if not user_has_starter_access(user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"{feature} is a Starter plan feature. "
                "Upgrade to Starter ($19/mo) to unlock it."
            ),
        )
