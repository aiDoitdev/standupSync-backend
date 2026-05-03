import os
import hmac
import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
import structlog

from database import get_db
from models import Team, User, Subscription, WebhookEvent
from schemas import CreateCheckoutRequest, CheckoutResponse, PortalResponse, BillingStatusResponse
from auth import get_current_user, require_manager, require_team_manager
from rate_limiter import limiter

logger = structlog.get_logger(__name__)

router = APIRouter()

LS_API_KEY             = os.getenv("LEMONSQUEEZY_API_KEY", "")
LS_WEBHOOK_SECRET      = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "")
LS_STORE_ID            = os.getenv("LEMONSQUEEZY_STORE_ID", "")
LS_STARTER_VARIANT_ID  = os.getenv("LEMONSQUEEZY_STARTER_VARIANT_ID", "")
FRONTEND_URL           = os.getenv("FRONTEND_URL", "http://localhost:3000")

LS_API_BASE = "https://api.lemonsqueezy.com/v1"


def _ls_headers() -> dict:
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {LS_API_KEY}",
    }


def _verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify Lemon Squeezy HMAC-SHA256 signature.
    Raises 500 (not silently returns False) when the secret is unconfigured —
    a missing secret is a deployment bug, not a bad request.
    """
    if not LS_WEBHOOK_SECRET:
        logger.error("billing.webhook.secret_missing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook secret is not configured. Contact support.",
        )
    digest = hmac.new(
        LS_WEBHOOK_SECRET.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def _parse_ls_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string from Lemon Squeezy into a naive UTC datetime."""
    if not value:
        return None
    try:
        # LS returns strings like "2025-05-01T00:00:00.000000Z"
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


# ── Checkout ──────────────────────────────��─────────────────────────────��────

@router.post("/checkout", response_model=CheckoutResponse)
@limiter.limit("5/minute")
async def create_checkout(
    request: Request,
    data: CreateCheckoutRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Create a Lemon Squeezy checkout session for upgrading to Starter."""
    log = logger.bind(team_id=data.team_id, user_id=str(current_user.id))
    team, _ = await require_team_manager(data.team_id, current_user, db)

    if current_user.plan == "starter" and current_user.plan_status == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account is already on the Starter plan.",
        )

    if not LS_API_KEY or not LS_STORE_ID or not LS_STARTER_VARIANT_ID:
        log.error("billing.checkout.misconfigured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment system is not configured. Please contact support.",
        )

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_options": {"embed": False, "media": False, "button_color": "#7c3aed"},
                "checkout_data": {
                    "email": current_user.email,
                    "name": current_user.name or "",
                    "custom": {"team_id": str(team.id), "user_id": str(current_user.id)},
                },
                "product_options": {
                    "redirect_url": f"{FRONTEND_URL}/dashboard/billing?team_id={team.id}&status=success",
                    "receipt_link_url": f"{FRONTEND_URL}/dashboard/billing?team_id={team.id}",
                },
            },
            "relationships": {
                "store":   {"data": {"type": "stores",   "id": LS_STORE_ID}},
                "variant": {"data": {"type": "variants", "id": LS_STARTER_VARIANT_ID}},
            },
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LS_API_BASE}/checkouts",
            json=payload,
            headers=_ls_headers(),
            timeout=15.0,
        )

    if resp.status_code not in (200, 201):
        log.error("billing.checkout.ls_error", http_status=resp.status_code, body=resp.text[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create checkout session. Please try again.",
        )

    checkout_url = resp.json()["data"]["attributes"]["url"]
    log.info("billing.checkout.created")
    return CheckoutResponse(checkout_url=checkout_url)


# ── Customer portal ─────────────────────────────────���─────────────────────────

@router.post("/portal", response_model=PortalResponse)
@limiter.limit("10/minute")
async def create_portal_session(
    request: Request,
    data: CreateCheckoutRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Return the Lemon Squeezy customer-portal URL for managing the subscription."""
    log = logger.bind(team_id=data.team_id, user_id=str(current_user.id))

    if not current_user.ls_customer_id or not current_user.ls_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active subscription found for your account.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LS_API_BASE}/subscriptions/{current_user.ls_subscription_id}",
            headers=_ls_headers(),
            timeout=15.0,
        )

    if resp.status_code != 200:
        log.error("billing.portal.ls_error", http_status=resp.status_code, body=resp.text[:500])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch subscription details.",
        )

    portal_url = resp.json()["data"]["attributes"]["urls"]["customer_portal"]
    return PortalResponse(portal_url=portal_url)


# ── Billing status ────────────────────────────────────────────────────────────

@router.get("/status/{team_id}", response_model=BillingStatusResponse)
async def billing_status(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current billing plan and status for the team's owning manager."""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Fetch the manager's billing info — billing lives on the user, not the team
    manager_result = await db.execute(select(User).where(User.id == team.manager_id))
    manager = manager_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team manager not found")

    return BillingStatusResponse(
        plan=manager.plan or "free",
        plan_status=manager.plan_status or "active",
        ls_subscription_id=manager.ls_subscription_id,
        ls_customer_id=manager.ls_customer_id,
        plan_expires_at=manager.plan_expires_at,
    )


# ── Webhook ────────────────────────────────��───────────────────────────────��──

@router.post("/webhook")
async def lemon_squeezy_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handle Lemon Squeezy webhook events.
    Auth: none — verified via HMAC-SHA256 signature.
    Idempotency: events deduplicated by meta.event_id in webhook_events table.
    Always returns 200 after signature check so LS does not retry on our logic errors.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-signature", "")

    # _verify_webhook_signature raises 500 if secret missing, returns False if sig invalid
    if not _verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    event: dict = json.loads(raw_body)
    meta        = event.get("meta", {})
    event_name  = meta.get("event_name", "")
    event_id    = meta.get("event_id", "")   # unique per delivery from LS
    custom_data = meta.get("custom_data", {})
    attributes  = event.get("data", {}).get("attributes", {})

    log = logger.bind(event_name=event_name, event_id=event_id)

    # ── Idempotency check ──────────────────────────────��──────────────────────
    if event_id:
        existing = await db.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == event_id)
        )
        if existing.scalar_one_or_none():
            log.info("billing.webhook.duplicate_skipped")
            return {"status": "ok", "note": "duplicate"}

    # ── Resolve user — billing is per user (manager), not per team ────────────
    user_id_str = custom_data.get("user_id") or ""

    # For renewal/status events user_id may not be in custom_data;
    # fall back to looking up by subscription ID.
    if not user_id_str:
        ls_sub_id = str(event.get("data", {}).get("id", ""))
        if ls_sub_id:
            found = await db.execute(
                select(User).where(User.ls_subscription_id == ls_sub_id)
            )
            user_obj = found.scalar_one_or_none()
            if user_obj:
                user_id_str = str(user_obj.id)

    if event_id:
        db.add(WebhookEvent(
            event_id=event_id,
            event_name=event_name,
            user_id=user_id_str or None,
            payload=raw_body.decode("utf-8"),
        ))
        # Flush now so the unique constraint fires before we do any more work;
        # commit happens at the end of the handler.
        await db.flush()

    log = log.bind(user_id=user_id_str)

    if not user_id_str:
        log.warning("billing.webhook.no_user_id")
        await db.commit()
        return {"status": "ignored", "reason": "no user_id"}

    result = await db.execute(select(User).where(User.id == user_id_str))
    user = result.scalar_one_or_none()
    if not user:
        log.warning("billing.webhook.user_not_found")
        await db.commit()
        return {"status": "ignored", "reason": "user not found"}

    ls_sub_id    = str(event.get("data", {}).get("id", ""))
    ls_cust_id   = str(attributes.get("customer_id", ""))
    ls_var_id    = str(attributes.get("variant_id", ""))
    period_start = _parse_ls_datetime(attributes.get("current_period_start") or attributes.get("created_at"))
    period_end   = _parse_ls_datetime(attributes.get("current_period_end") or attributes.get("ends_at") or attributes.get("renews_at"))

    # ── Event handlers ────────────────────────────────────────────────────��───

    if event_name == "subscription_created":
        user.plan              = "starter"
        user.plan_status       = "active"
        user.plan_expires_at   = None
        user.ls_subscription_id = ls_sub_id
        user.ls_customer_id    = ls_cust_id
        user.ls_variant_id     = ls_var_id
        db.add(user)

        db.add(Subscription(
            user_id=user.id, ls_subscription_id=ls_sub_id,
            ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id,
            plan="starter", status="active",
            current_period_start=period_start, current_period_end=period_end,
        ))
        log.info("billing.webhook.subscription_created")

    elif event_name == "subscription_updated":
        ls_status = attributes.get("status", "")

        if ls_status == "active":
            user.plan              = "starter"
            user.plan_status       = "active"
            user.plan_expires_at   = None
            user.ls_subscription_id = ls_sub_id
            user.ls_customer_id    = ls_cust_id

        elif ls_status == "past_due":
            user.plan_status = "past_due"

        elif ls_status in ("cancelled", "expired"):
            # Grace period: keep Starter features until the billing period ends
            user.plan              = "starter"   # still on starter until grace period expires
            user.plan_status       = "canceled"
            user.plan_expires_at   = period_end
            user.ls_subscription_id = None

        db.add(user)

        db.add(Subscription(
            user_id=user.id, ls_subscription_id=ls_sub_id,
            ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id,
            plan="starter" if ls_status not in ("cancelled", "expired") else "free",
            status=ls_status,
            current_period_start=period_start, current_period_end=period_end,
            canceled_at=datetime.utcnow() if ls_status in ("cancelled", "expired") else None,
        ))
        log.info("billing.webhook.subscription_updated", ls_status=ls_status)

    elif event_name == "subscription_cancelled":
        user.plan              = "starter"   # access retained until billing period ends
        user.plan_status       = "canceled"
        user.plan_expires_at   = period_end
        user.ls_subscription_id = None
        db.add(user)

        db.add(Subscription(
            user_id=user.id, ls_subscription_id=ls_sub_id,
            ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id,
            plan="starter", status="canceled",
            current_period_start=period_start, current_period_end=period_end,
            canceled_at=datetime.utcnow(),
        ))
        log.info("billing.webhook.subscription_cancelled", grace_until=str(period_end))

    elif event_name == "subscription_payment_failed":
        user.plan_status = "past_due"
        db.add(user)

        db.add(Subscription(
            user_id=user.id, ls_subscription_id=ls_sub_id,
            ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id,
            plan=user.plan, status="past_due",
            current_period_start=period_start, current_period_end=period_end,
        ))
        log.warning("billing.webhook.payment_failed")

    else:
        log.info("billing.webhook.unhandled_event")

    await db.commit()
    return {"status": "ok"}
