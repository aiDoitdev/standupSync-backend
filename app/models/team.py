from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey,
    Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, FullTimestampMixin


class Team(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "teams"

    name       = Column(String(255), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    timezone   = Column(String(100), nullable=False, default="Asia/Kolkata")
    team_type  = Column(String(50), nullable=True)
    hourly_rate = Column(Float, nullable=True)
    currency    = Column(String(10), nullable=False, default="INR")
    # Legacy question labels — kept for backward compatibility with existing data
    q1_label = Column(String(255), default="What did you accomplish yesterday?")
    q2_label = Column(String(255), default="What will you work on today?")
    q3_label = Column(String(255), default="Any blockers or issues?")

    __table_args__ = (
        Index("ix_teams_manager_id", "manager_id"),
    )


class TeamMember(UUIDPrimaryKeyMixin, FullTimestampMixin, Base):
    __tablename__ = "team_members"

    team_id    = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status     = Column(String(20), nullable=False, default="pending")
    role       = Column(String(20), nullable=False, default="member")
    hourly_rate = Column(Float, nullable=True)
    timezone   = Column(String(100), nullable=False, default="Asia/Kolkata")
    send_time  = Column(String(5), nullable=False, default="09:00")
    currency   = Column(String(10), nullable=False, default="INR")
    hours_per_day = Column(Float, nullable=True)
    hours_confirmed = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        CheckConstraint("status IN ('pending', 'active', 'inactive')", name="ck_team_members_status"),
        CheckConstraint("role IN ('member', 'co-manager')", name="ck_team_members_role"),
        Index("ix_team_members_team_status", "team_id", "status"),
        Index("ix_team_members_user_id", "user_id"),
    )


class TeamQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "team_questions"

    team_id     = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    label       = Column(String(500), nullable=False)
    enabled     = Column(Boolean, nullable=False, default=True)
    is_blocker_type = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_team_questions_team_order", "team_id", "order_index"),
    )


class Invite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "invites"

    team_id    = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    email      = Column(String(255), nullable=False)
    token      = Column(String(255), unique=True, nullable=False, index=True)
    used       = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_invites_team_email", "team_id", "email"),
    )


class WaitlistEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "waitlist"

    email = Column(String(255), unique=True, nullable=False, index=True)
