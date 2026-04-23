from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select, and_

from app.core.database import AsyncSessionLocal
from app.core.config import get_settings
from app.models.user import User
from app.models.billing import Subscription

logger = structlog.get_logger(__name__)
_settings = get_settings()

LS_API_BASE = "https://api.lemonsqueezy.com/v1"


def _ls_headers() -> dict:
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {_settings.lemonsqueezy_api_key}",
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


async def reconcile_subscriptions() -> None:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    async with AsyncSessionLocal() as db:
        # Expire grace periods that have passed — downgrade user to free
        grace_users = (await db.execute(
            select(User).where(and_(
                User.plan_status == "canceled",
                User.plan_expires_at.isnot(None),
                User.plan_expires_at <= now_utc,
            ))
        )).scalars().all()
        for user in grace_users:
            user.plan = "free"
            user.plan_expires_at = None
            db.add(user)
            logger.info("billing_job.grace_period_expired", user_id=str(user.id))

        # Collect active Starter users with a live LS subscription for sync
        active_users = (await db.execute(
            select(User).where(and_(
                User.plan == "starter",
                User.ls_subscription_id.isnot(None),
            ))
        )).scalars().all()
        await db.commit()

    for user in active_users:
        if not _settings.lemonsqueezy_api_key:
            break
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{LS_API_BASE}/subscriptions/{user.ls_subscription_id}",
                    headers=_ls_headers(),
                    timeout=10.0,
                )
            if resp.status_code != 200:
                continue

            attrs = resp.json()["data"]["attributes"]
            ls_status = attrs.get("status", "")
            period_end = _parse_dt(
                attrs.get("current_period_end") or attrs.get("ends_at") or attrs.get("renews_at")
            )

            if ls_status in ("cancelled", "expired") and user.plan_status == "active":
                async with AsyncSessionLocal() as db:
                    fresh = (await db.execute(select(User).where(User.id == user.id))).scalar_one_or_none()
                    if fresh:
                        fresh.plan_status = "canceled"
                        fresh.plan_expires_at = period_end
                        fresh.ls_subscription_id = None
                        db.add(fresh)
                        db.add(Subscription(
                            user_id=fresh.id,
                            ls_subscription_id=user.ls_subscription_id,
                            plan="starter",
                            status="canceled",
                            current_period_end=period_end,
                            canceled_at=now_utc,
                        ))
                        await db.commit()
                        logger.info("billing_job.subscription_corrected", user_id=str(user.id), ls_status=ls_status)
        except Exception as exc:
            logger.warning("billing_job.check_failed", user_id=str(user.id), error=str(exc))

    logger.info("billing_job.done", checked=len(active_users))
