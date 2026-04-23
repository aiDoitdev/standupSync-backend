from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, FullTimestampMixin


class Subscription(UUIDPrimaryKeyMixin, FullTimestampMixin, Base):
    """Full subscription lifecycle history — one row per status transition."""
    __tablename__ = "subscriptions"

    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ls_subscription_id   = Column(String(255), nullable=False)
    ls_customer_id       = Column(String(255), nullable=True)
    ls_variant_id        = Column(String(255), nullable=True)
    plan                 = Column(String(20), nullable=False)
    status               = Column(String(20), nullable=False)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end   = Column(DateTime, nullable=True)
    canceled_at          = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("plan IN ('free', 'starter')", name="ck_subscriptions_plan"),
        Index("ix_subscriptions_user_id", "user_id"),
        Index("ix_subscriptions_ls_sub_id", "ls_subscription_id"),
    )


class WebhookEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Idempotency store for Lemon Squeezy webhook events."""
    __tablename__ = "webhook_events"

    event_id   = Column(String(255), unique=True, nullable=False, index=True)
    event_name = Column(String(100), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    payload    = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_webhook_events_event_id", "event_id"),
        Index("ix_webhook_events_user_id", "user_id"),
    )
