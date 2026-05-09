from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, UUIDPrimaryKeyMixin, TimestampMixin, FullTimestampMixin


class AutomationAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-team AI analysis runs (Automation Radar + AI Task Radar)."""
    __tablename__ = "automation_analyses"

    team_id           = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    created_by        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    window_days       = Column(Integer, nullable=False, default=14)
    status            = Column(String(20), nullable=False, default="completed")
    period_start      = Column(Date, nullable=False)
    period_end        = Column(Date, nullable=False)
    findings_json     = Column(JSONB, nullable=True)
    summary_text      = Column(Text, nullable=True)
    error_message     = Column(Text, nullable=True)
    trigger           = Column(String(20), nullable=False, default="manual_admin")
    team_score        = Column(Integer, nullable=True)
    member_count      = Column(Integer, nullable=True)
    task_count        = Column(Integer, nullable=True)
    is_empty          = Column(Boolean, nullable=False, default=False)
    llm_response_json = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "period_start", name="uq_automation_analyses_team_period"),
        CheckConstraint("status IN ('completed', 'failed')", name="ck_automation_analyses_status"),
        CheckConstraint(
            "trigger IN ('scheduled', 'manual_admin', 'initial', 'manual')",
            name="ck_automation_analyses_trigger",
        ),
        Index("ix_automation_analyses_team_id", "team_id"),
        Index("ix_automation_analyses_team_created", "team_id", "created_at"),
    )


class AutomationSchedule(FullTimestampMixin, Base):
    """Per-team weekly AI Task Radar schedule."""
    __tablename__ = "automation_schedules"

    team_id       = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    cadence       = Column(String(20), nullable=False, default="weekly")
    day_of_week   = Column(Integer, nullable=False, default=0)
    week_of_month = Column(Integer, nullable=True)
    run_time      = Column(String(5), nullable=False, default="08:00")
    timezone      = Column(String(100), nullable=False, default="Asia/Kolkata")
    enabled       = Column(Boolean, nullable=False, default=True)
    next_run_at   = Column(DateTime, nullable=True)
    last_run_at   = Column(DateTime, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("cadence IN ('weekly', 'biweekly', 'monthly')", name="ck_automation_schedules_cadence"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_automation_schedules_dow"),
    )


class AutomationTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Normalised per-task output of the AI Task Radar LLM call."""
    __tablename__ = "automation_tasks"

    analysis_id          = Column(UUID(as_uuid=True), ForeignKey("automation_analyses.id", ondelete="CASCADE"), nullable=False)
    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_name        = Column(String(255), nullable=True)
    task_title           = Column(String(500), nullable=False)
    task_description     = Column(Text, nullable=True)
    automation_score     = Column(Integer, nullable=False, default=0)
    tier                 = Column(String(4), nullable=False, default="P3")
    suggested_tools_json = Column(JSONB, nullable=False, default=list)
    suggested_workflow   = Column(Text, nullable=True)
    general_suggestion   = Column(Text, nullable=True)
    source               = Column(String(32), nullable=False, default="checkin")
    source_ref           = Column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint("tier IN ('P1', 'P2', 'P3')", name="ck_automation_tasks_tier"),
        CheckConstraint("automation_score >= 0 AND automation_score <= 100", name="ck_automation_tasks_score"),
        Index("ix_automation_tasks_analysis_id", "analysis_id"),
    )


class AutomationIntegration(UUIDPrimaryKeyMixin, FullTimestampMixin, Base):
    """Stub for future Jira/Linear/Notion integrations."""
    __tablename__ = "automation_integrations"

    team_id     = Column(UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    provider    = Column(String(20), nullable=False)
    status      = Column(String(20), nullable=False, default="disconnected")
    config_json = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("team_id", "provider", name="uq_automation_integrations_team_provider"),
        CheckConstraint(
            "provider IN ('jira', 'linear', 'notion', 'sheets')",
            name="ck_automation_integrations_provider",
        ),
        Index("ix_automation_integrations_team_id", "team_id"),
    )
