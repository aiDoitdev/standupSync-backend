from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class BillingStatusResponse(BaseModel):
    plan: str
    plan_status: str
    ls_subscription_id: Optional[str] = None
    ls_customer_id: Optional[str] = None
    plan_expires_at: Optional[datetime] = None
