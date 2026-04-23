from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin


class OTPVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "otp_verifications"

    email      = Column(String(255), nullable=False)
    otp_code   = Column(String(10), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used       = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_otp_email_used", "email", "used"),
    )


class Checkin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checkins"

    team_id       = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    user_id       = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date          = Column(Date, nullable=False)
    # Legacy fixed-column answers kept for backward compatibility with pre-dynamic-question data
    yesterday     = Column(Text, nullable=True)
    today         = Column(Text, nullable=True)
    blockers      = Column(Text, nullable=True)
    checkin_token = Column(String(255), unique=True, nullable=True, index=True)
    token_used    = Column(Boolean, nullable=False, default=False)
    submitted_at  = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "user_id", "date", name="uq_checkins_team_user_date"),
        Index("ix_checkins_team_date", "team_id", "date"),
        Index("ix_checkins_user_date", "user_id", "date"),
    )


class CheckinAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checkin_answers"

    checkin_id  = Column(UUID(as_uuid=True), ForeignKey("checkins.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("team_questions.id", ondelete="CASCADE"), nullable=False)
    answer      = Column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("checkin_id", "question_id", name="uq_checkin_answers_checkin_question"),
        Index("ix_checkin_answers_checkin_id", "checkin_id"),
    )
