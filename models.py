import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, Boolean, DateTime, Date, Text, ForeignKey, Float, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    password = Column(String(255), nullable=False)  # bcrypt hashed
    role = Column(String(20), default="member")      # 'manager' | 'member'
    created_at = Column(DateTime, default=datetime.utcnow)
    # Account-level billing — one subscription covers all teams this manager owns
    plan               = Column(String(20), default="free")
    plan_status        = Column(String(20), default="active")
    plan_expires_at    = Column(DateTime, nullable=True)
    ls_customer_id     = Column(String(255), nullable=True)
    ls_subscription_id = Column(String(255), nullable=True)
    ls_variant_id      = Column(String(255), nullable=True)


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    # timezone kept for backward compatibility; per-member timezone now lives on TeamMember
    timezone = Column(String(100), default="Asia/Kolkata")
    team_type   = Column(String(50), nullable=True)
    hourly_rate = Column(Float, nullable=True)
    # currency kept for backward compatibility; per-member currency now lives on TeamMember
    currency    = Column(String(10), default="INR")
    # q*_label kept for backward compatibility; questions now live in TeamQuestion table
    q1_label    = Column(String(255), default="What did you accomplish yesterday?")
    q2_label    = Column(String(255), default="What will you work on today?")
    q3_label    = Column(String(255), default="Any blockers or issues?")
    created_at  = Column(DateTime, default=datetime.utcnow)


class TeamMember(Base):
    __tablename__ = "team_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    status = Column(String(20), default="pending")  # 'pending' | 'active' | 'inactive'
    role = Column(String(20), default="member")      # 'member' | 'co-manager'
    hourly_rate = Column(Float, nullable=True)
    # Per-member timezone, send_time, and currency (Issues 1 & 2)
    timezone = Column(String(100), default="Asia/Kolkata")
    send_time = Column(String(5), default="09:00")   # "HH:MM" in member's timezone
    currency = Column(String(10), default="INR")
    # Cost Intelligence columns
    hours_per_day = Column(Float, nullable=True)        # confirmed working hours per day
    hours_confirmed = Column(Boolean, default=False, nullable=False)  # member confirmed their hours
    created_at = Column(DateTime, default=datetime.utcnow)


class TeamQuestion(Base):
    """Configurable standup questions per team (Issue 4)."""
    __tablename__ = "team_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    label = Column(String(500), nullable=False)
    enabled = Column(Boolean, default=True)
    is_blocker_type = Column(Boolean, default=False)  # auto-creates Blocker record when answered
    created_at = Column(DateTime, default=datetime.utcnow)


class Invite(Base):
    __tablename__ = "invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"))
    email = Column(String(255), nullable=False)
    token = Column(String(255), unique=True, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, default=lambda: datetime.utcnow() + timedelta(days=7))


class WaitlistEntry(Base):
    __tablename__ = "waitlist"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Checkin(Base):
    __tablename__ = "checkins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    date = Column(Date, nullable=False)
    # Legacy fixed-column answers (kept for backward compatibility with old data)
    yesterday = Column(Text)
    today = Column(Text)
    blockers = Column(Text)
    checkin_token = Column(String(255), unique=True)  # magic link token
    token_used = Column(Boolean, default=False)
    submitted_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class CheckinAnswer(Base):
    """Dynamic answers for configurable questions (Issue 4)."""
    __tablename__ = "checkin_answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    checkin_id = Column(UUID(as_uuid=True), ForeignKey("checkins.id"), nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("team_questions.id"), nullable=False)
    answer = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class Blocker(Base):
    __tablename__ = "blockers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    checkin_id = Column(UUID(as_uuid=True), ForeignKey("checkins.id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)  # member who reported
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # member assigned by manager
    status = Column(String(20), default="open")  # 'open' | 'acknowledged' | 'in_progress' | 'resolved'
    title = Column(String(255), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


class BlockerComment(Base):
    __tablename__ = "blocker_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blocker_id = Column(UUID(as_uuid=True), ForeignKey("blockers.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    comment = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BlockerResolution(Base):
    __tablename__ = "blocker_resolutions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blocker_id = Column(UUID(as_uuid=True), ForeignKey("blockers.id"), nullable=False)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    unblock_instructions = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class OTPVerification(Base):
    """Short-lived OTP records used for email verification during signup."""
    __tablename__ = "otp_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    otp_code = Column(String(10), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AutomationAnalysis(Base):
    """Stores per-team AI analysis runs (legacy Automation Radar + new Ai Task Radar)."""
    __tablename__ = "automation_analyses"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id       = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    created_by    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    window_days   = Column(Integer, nullable=False, default=14)
    status        = Column(String(20), nullable=False, default="completed")  # 'completed' | 'failed'
    period_start  = Column(Date, nullable=False)
    period_end    = Column(Date, nullable=False)
    findings_json = Column(Text, nullable=True)   # JSON list of finding dicts (legacy view)
    summary_text  = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    # Ai Task Radar columns (migration 2)
    trigger           = Column(String(20), nullable=False, default="manual_admin")  # 'scheduled' | 'manual_admin' | 'initial'
    team_score        = Column(Integer, nullable=True)    # 0..100
    member_count      = Column(Integer, nullable=True)
    task_count        = Column(Integer, nullable=True)
    is_empty          = Column(Boolean, nullable=False, default=False)
    llm_response_json = Column(Text, nullable=True)   # raw JSON blob returned by the LLM; avoids re-calling on repeat fetches
    created_at        = Column(DateTime, nullable=False, default=datetime.utcnow, server_default=func.now())


class AutomationSchedule(Base):
    """Per-team weekly Ai Task Radar schedule (cadence + day + time + timezone)."""
    __tablename__ = "automation_schedules"

    team_id        = Column(UUID(as_uuid=True), ForeignKey("teams.id"), primary_key=True)
    cadence        = Column(String(20), nullable=False, default="weekly")  # weekly | biweekly | monthly
    day_of_week    = Column(Integer, nullable=False, default=0)  # 0=Mon ... 6=Sun
    week_of_month  = Column(Integer, nullable=True)  # 1..4 for monthly
    run_time       = Column(String(5), nullable=False, default="08:00")  # HH:MM in `timezone`
    timezone       = Column(String(100), nullable=False, default="Asia/Kolkata")
    enabled        = Column(Boolean, nullable=False, default=True)
    next_run_at    = Column(DateTime, nullable=True)  # UTC
    last_run_at    = Column(DateTime, nullable=True)
    failure_count  = Column(Integer, nullable=False, default=0)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AutomationTask(Base):
    """Normalised per-task output of the Ai Task Radar LLM call (one row per inferred task)."""
    __tablename__ = "automation_tasks"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    analysis_id          = Column(UUID(as_uuid=True), ForeignKey("automation_analyses.id", ondelete="CASCADE"), nullable=False)
    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_name        = Column(String(255), nullable=True)      # echoed from LLM for display when user_id is null
    task_title           = Column(String(500), nullable=False)
    task_description     = Column(Text, nullable=True)
    automation_score     = Column(Integer, nullable=False, default=0)   # 0..100
    tier                 = Column(String(4), nullable=False, default="P3")  # P1 | P2 | P3
    suggested_tools_json = Column(Text, nullable=False, default="[]")   # JSON-encoded [{name, prompt}]
    suggested_workflow   = Column(Text, nullable=True)
    general_suggestion   = Column(Text, nullable=True)
    source               = Column(String(32), nullable=False, default="checkin")
    source_ref           = Column(String(255), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)


class Subscription(Base):
    """Full subscription lifecycle history. One row per status transition."""
    __tablename__ = "subscriptions"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id              = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ls_subscription_id   = Column(String(255), nullable=False, index=True)
    ls_customer_id       = Column(String(255), nullable=True)
    ls_variant_id        = Column(String(255), nullable=True)
    plan                 = Column(String(20), nullable=False)           # 'free' | 'starter'
    status               = Column(String(20), nullable=False)           # 'active' | 'canceled' | 'past_due' | 'expired'
    current_period_start = Column(DateTime, nullable=True)
    current_period_end   = Column(DateTime, nullable=True)              # used as grace-period boundary
    canceled_at          = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebhookEvent(Base):
    """Idempotency store for Lemon Squeezy webhook events."""
    __tablename__ = "webhook_events"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # LS sends a unique event ID in meta.event_id — use it as idempotency key
    event_id   = Column(String(255), unique=True, nullable=False, index=True)
    event_name = Column(String(100), nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    payload    = Column(Text, nullable=False)   # raw JSON body
    created_at = Column(DateTime, default=datetime.utcnow)


class AutomationIntegration(Base):
    """Stub row for future Jira/Linear/Notion integrations. Schema ready; UI shows 'Coming soon'."""
    __tablename__ = "automation_integrations"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id     = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    provider    = Column(String(20), nullable=False)  # jira | linear | notion
    status      = Column(String(20), nullable=False, default="disconnected")
    config_json = Column(Text, nullable=True)  # JSON-encoded integration config
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
