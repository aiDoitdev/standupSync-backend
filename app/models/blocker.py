from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, FullTimestampMixin


class Blocker(UUIDPrimaryKeyMixin, FullTimestampMixin, Base):
    __tablename__ = "blockers"

    team_id     = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    checkin_id  = Column(UUID(as_uuid=True), ForeignKey("checkins.id", ondelete="SET NULL"), nullable=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status      = Column(String(20), nullable=False, default="open")
    title       = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'in_progress', 'resolved')",
            name="ck_blockers_status",
        ),
        Index("ix_blockers_team_status", "team_id", "status"),
        Index("ix_blockers_user_id", "user_id"),
        Index("ix_blockers_assigned_to", "assigned_to"),
    )


class BlockerComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "blocker_comments"

    blocker_id = Column(UUID(as_uuid=True), ForeignKey("blockers.id", ondelete="CASCADE"), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    comment    = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_blocker_comments_blocker_id", "blocker_id"),
    )


class BlockerResolution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "blocker_resolutions"

    blocker_id           = Column(UUID(as_uuid=True), ForeignKey("blockers.id", ondelete="CASCADE"), nullable=False)
    manager_id           = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    unblock_instructions = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_blocker_resolutions_blocker_id", "blocker_id"),
    )
