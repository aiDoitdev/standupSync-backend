import hmac
import hashlib
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
import structlog

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_manager
from app.core.config import get_settings
from app.models.user import User
from app.models.billing import Subscription, WebhookEvent
from app.schemas.billing import CheckoutResponse, PortalResponse, BillingStatusResponse
from app.utils.rate_limiter import limiter

logger = structlog.get_logger(__name__)
router = APIRouter()

_settings = get_settings()
LS_API_BASE = "https://api.lemonsqueezy.com/v1"


def _ls_headers() -> dict:
    return {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "Authorization": f"Bearer {_settings.lemonsqueezy_api_key}",
    }


def _verify_webhook_signature(payload: bytes, signature: str) -> bool:
    secret = _settings.lemonsqueezy_webhook_secret
    if not secret:
        logger.error("billing.webhook.secret_missing")
        raise HTTPException(status_code=500, detail="Webhook secret is not configured.")
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def _parse_ls_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


@router.post("/checkout", response_model=CheckoutResponse)
@limiter.limit("5/minute")
async def create_checkout(
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Create a Lemon Squeezy checkout session for the current user's account.
    One payment upgrades all teams this manager owns."""
    log = logger.bind(user_id=str(current_user.id))

    if current_user.plan == "starter" and current_user.plan_status == "active":
        raise HTTPException(status_code=400, detail="Your account is already on the Starter plan.")

    if not all([_settings.lemonsqueezy_api_key, _settings.lemonsqueezy_store_id, _settings.lemonsqueezy_starter_variant_id]):
        log.error("billing.checkout.misconfigured")
        raise HTTPException(status_code=503, detail="Payment system is not configured.")

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_options": {"embed": False, "media": False, "button_color": "#7c3aed"},
                "checkout_data": {
                    "email": current_user.email,
                    "name": current_user.name or "",
                    "custom": {"user_id": str(current_user.id)},
                },
                "product_options": {
                    "redirect_url": f"{_settings.frontend_url}/dashboard/billing?status=success",
                    "receipt_link_url": f"{_settings.frontend_url}/dashboard/billing",
                },
            },
            "relationships": {
                "store": {"data": {"type": "stores", "id": _settings.lemonsqueezy_store_id}},
                "variant": {"data": {"type": "variants", "id": _settings.lemonsqueezy_starter_variant_id}},
            },
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{LS_API_BASE}/checkouts", json=payload, headers=_ls_headers(), timeout=15.0)
    if resp.status_code not in (200, 201):
        log.error("billing.checkout.ls_error", status=resp.status_code)
        raise HTTPException(status_code=502, detail="Failed to create checkout session.")
    log.info("billing.checkout.created")
    return CheckoutResponse(checkout_url=resp.json()["data"]["attributes"]["url"])


@router.post("/portal", response_model=PortalResponse)
@limiter.limit("10/minute")
async def create_portal_session(
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Open the Lemon Squeezy customer portal for the current user's subscription."""
    if not current_user.ls_customer_id or not current_user.ls_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription found for your account.")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LS_API_BASE}/subscriptions/{current_user.ls_subscription_id}",
            headers=_ls_headers(),
            timeout=15.0,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch subscription details.")
    return PortalResponse(portal_url=resp.json()["data"]["attributes"]["urls"]["customer_portal"])


@router.get("/status", response_model=BillingStatusResponse)
async def billing_status(current_user: User = Depends(get_current_user)):
    """Return the billing status for the current user's account."""
    return BillingStatusResponse(
        plan=current_user.plan,
        plan_status=current_user.plan_status,
        ls_subscription_id=current_user.ls_subscription_id,
        ls_customer_id=current_user.ls_customer_id,
        plan_expires_at=current_user.plan_expires_at,
    )


@router.post("/webhook")
async def lemon_squeezy_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("x-signature", "")
    if not _verify_webhook_signature(raw_body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    event = json.loads(raw_body)
    meta = event.get("meta", {})
    event_name = meta.get("event_name", "")
    event_id = meta.get("event_id", "")
    custom_data = meta.get("custom_data", {})
    attributes = event.get("data", {}).get("attributes", {})
    log = logger.bind(event_name=event_name, event_id=event_id)

    # Idempotency: skip duplicate webhook deliveries
    if event_id:
        if (await db.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))).scalar_one_or_none():
            log.info("billing.webhook.duplicate_skipped")
            return {"status": "ok", "note": "duplicate"}

    # Resolve user_id — prefer custom_data, fall back to ls_subscription_id lookup
    user_id_str = custom_data.get("user_id") or ""
    if not user_id_str:
        ls_sub_id_fallback = str(event.get("data", {}).get("id", ""))
        if ls_sub_id_fallback:
            user_obj = (await db.execute(
                select(User).where(User.ls_subscription_id == ls_sub_id_fallback)
            )).scalar_one_or_none()
            if user_obj:
                user_id_str = str(user_obj.id)

    # Persist webhook event for audit/idempotency
    if event_id:
        db.add(WebhookEvent(
            event_id=event_id,
            event_name=event_name,
            user_id=user_id_str or None,
            payload=raw_body.decode(),
        ))
        await db.flush()

    if not user_id_str:
        log.warning("billing.webhook.no_user_id")
        await db.commit()
        return {"status": "ignored", "reason": "no user_id"}

    user = (await db.execute(select(User).where(User.id == user_id_str))).scalar_one_or_none()
    if not user:
        log.warning("billing.webhook.user_not_found")
        await db.commit()
        return {"status": "ignored", "reason": "user not found"}

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    ls_sub_id = str(event.get("data", {}).get("id", ""))
    ls_cust_id = str(attributes.get("customer_id", ""))
    ls_var_id = str(attributes.get("variant_id", ""))
    period_start = _parse_ls_datetime(attributes.get("current_period_start") or attributes.get("created_at"))
    period_end = _parse_ls_datetime(attributes.get("current_period_end") or attributes.get("ends_at") or attributes.get("renews_at"))

    if event_name == "subscription_created":
        user.plan, user.plan_status, user.plan_expires_at = "starter", "active", None
        user.ls_subscription_id, user.ls_customer_id, user.ls_variant_id = ls_sub_id, ls_cust_id, ls_var_id
        db.add(user)
        db.add(Subscription(user_id=user.id, ls_subscription_id=ls_sub_id, ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id, plan="starter", status="active", current_period_start=period_start, current_period_end=period_end))
        log.info("billing.webhook.subscription_created")

    elif event_name == "subscription_updated":
        ls_status = attributes.get("status", "")
        if ls_status == "active":
            user.plan, user.plan_status, user.plan_expires_at = "starter", "active", None
            user.ls_subscription_id, user.ls_customer_id = ls_sub_id, ls_cust_id
        elif ls_status == "past_due":
            user.plan_status = "past_due"
        elif ls_status in ("cancelled", "expired"):
            user.plan, user.plan_status, user.plan_expires_at, user.ls_subscription_id = "starter", "canceled", period_end, None
        db.add(user)
        db.add(Subscription(user_id=user.id, ls_subscription_id=ls_sub_id, ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id, plan="starter" if ls_status not in ("cancelled", "expired") else "free", status=ls_status, current_period_start=period_start, current_period_end=period_end, canceled_at=now_utc if ls_status in ("cancelled", "expired") else None))
        log.info("billing.webhook.subscription_updated", ls_status=ls_status)

    elif event_name == "subscription_cancelled":
        user.plan, user.plan_status, user.plan_expires_at, user.ls_subscription_id = "starter", "canceled", period_end, None
        db.add(user)
        db.add(Subscription(user_id=user.id, ls_subscription_id=ls_sub_id, ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id, plan="starter", status="canceled", current_period_start=period_start, current_period_end=period_end, canceled_at=now_utc))
        log.info("billing.webhook.subscription_cancelled")

    elif event_name == "subscription_payment_failed":
        user.plan_status = "past_due"
        db.add(user)
        db.add(Subscription(user_id=user.id, ls_subscription_id=ls_sub_id, ls_customer_id=ls_cust_id, ls_variant_id=ls_var_id, plan=user.plan, status="past_due", current_period_start=period_start, current_period_end=period_end))
        log.warning("billing.webhook.payment_failed")
    else:
        log.info("billing.webhook.unhandled_event")

    await db.commit()
    return {"status": "ok"}
