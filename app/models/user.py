from sqlalchemy import CheckConstraint, Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email    = Column(String(255), unique=True, nullable=False, index=True)
    name     = Column(String(255), nullable=True)
    password = Column(String(255), nullable=False)
    role     = Column(String(20), nullable=False, default="member")

    # Account-level billing — one subscription covers all teams this manager owns
    plan               = Column(String(20), nullable=False, default="free")
    plan_status        = Column(String(20), nullable=False, default="active")
    plan_expires_at    = Column(DateTime, nullable=True)
    ls_customer_id     = Column(String(255), nullable=True)
    ls_subscription_id = Column(String(255), nullable=True)
    ls_variant_id      = Column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('manager', 'member')", name="ck_users_role"),
        CheckConstraint("plan IN ('free', 'starter')", name="ck_users_plan"),
        CheckConstraint("plan_status IN ('active', 'past_due', 'canceled')", name="ck_users_plan_status"),
        Index("ix_users_email_lower", "email", postgresql_using="btree"),
        Index("ix_users_ls_subscription_id", "ls_subscription_id"),
    )
