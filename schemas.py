from pydantic import BaseModel, BeforeValidator, field_validator
from typing import Annotated, Any, Optional, List, Literal
from datetime import datetime, date
import re

from email_validator import validate_email, EmailNotValidError


def _validate_email_lenient(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("string required")
    try:
        return validate_email(v, check_deliverability=False).normalized
    except EmailNotValidError:
        # Allow reserved/special-use TLDs (e.g. .test, .localhost) in dev environments
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            return v.lower()
        raise ValueError(f"value is not a valid email address: {v}")


LenientEmailStr = Annotated[str, BeforeValidator(_validate_email_lenient)]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: LenientEmailStr
    name: str
    password: str


class SendOTPRequest(BaseModel):
    email: LenientEmailStr
    name: str
    password: str


class VerifySignupRequest(BaseModel):
    email: LenientEmailStr
    name: str
    password: str
    otp_code: str


class LoginRequest(BaseModel):
    email: LenientEmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    name: str
    role: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    role: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

class CreateTeamRequest(BaseModel):
    name: str
    team_type: Optional[str] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    plan: str
    member_count: int


class InviteMembersRequest(BaseModel):
    emails: List[LenientEmailStr]


class MemberStatusResponse(BaseModel):
    user_id: str
    name: Optional[str]
    email: str
    member_status: str          # 'pending' | 'active'
    checked_in_today: bool
    submitted_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Team Questions (Issue 4)
# ---------------------------------------------------------------------------

CanonicalKind = Literal["yesterday", "today", "wins", "blockers", "other"]


class TeamQuestionResponse(BaseModel):
    id: str
    team_id: str
    order_index: int
    label: str
    enabled: bool
    is_blocker_type: bool
    canonical_kind: Optional[CanonicalKind] = None
    created_at: datetime


class TeamQuestionCreateRequest(BaseModel):
    label: str
    enabled: bool = True
    is_blocker_type: bool = False
    canonical_kind: Optional[CanonicalKind] = None


class TeamQuestionUpdateRequest(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    is_blocker_type: Optional[bool] = None
    order_index: Optional[int] = None
    canonical_kind: Optional[CanonicalKind] = None


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------

class QuestionItem(BaseModel):
    """Question info returned to the check-in magic-link page."""
    id: str
    label: str
    is_blocker_type: bool
    required: bool = True


class CheckinTokenResponse(BaseModel):
    member_name: str
    team_name: str
    date: str
    already_submitted: bool
    questions: List[QuestionItem] = []
    # Cost Intelligence: prompt member to confirm their hours on the check-in page
    hours_confirmation_needed: bool = False
    hours_per_day: Optional[float] = None


class AnswerItem(BaseModel):
    question_id: str
    answer: str


class SubmitCheckinRequest(BaseModel):
    answers: List[AnswerItem]


class CheckinAnswerResponse(BaseModel):
    question_id: str
    question_label: str
    answer: str
    is_blocker_type: bool


class CheckinResponse(BaseModel):
    id: str
    user_id: str
    member_name: Optional[str]
    date: str
    # Legacy fields (populated for old checkins that pre-date dynamic questions)
    yesterday: Optional[str]
    today: Optional[str]
    blockers: Optional[str]
    # Dynamic answers (populated for new checkins)
    answers: List[CheckinAnswerResponse] = []
    submitted_at: Optional[datetime]


class CheckinHistoryItem(BaseModel):
    date: str
    yesterday: Optional[str]
    today: Optional[str]
    blockers: Optional[str]
    answers: List[CheckinAnswerResponse] = []
    submitted_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Invite
# ---------------------------------------------------------------------------

class InviteInfoResponse(BaseModel):
    email: str
    team_name: str


class PendingInviteResponse(BaseModel):
    id: str
    email: str
    created_at: datetime
    expires_at: datetime


class AcceptInviteRequest(BaseModel):
    name: str
    password: str


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------

class BlockerCommentResponse(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str]
    comment: str
    created_at: datetime


class BlockerResolutionResponse(BaseModel):
    id: str
    manager_id: str
    manager_name: Optional[str]
    unblock_instructions: str
    created_at: datetime


class BlockerDetailResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    user_name: Optional[str]
    user_email: Optional[str]
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    status: str
    title: str
    description: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    resolved_at: Optional[datetime]
    comments: List[BlockerCommentResponse] = []
    resolution: Optional[BlockerResolutionResponse] = None


class BlockerListItemResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    user_name: Optional[str]
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    status: str
    title: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    comment_count: int
    resolved_at: Optional[datetime]
    # Blocker Intelligence extensions (BI-4)
    category: Optional[str] = None
    revenue_impact_usd: Optional[float] = None
    revenue_impact_label: Optional[str] = None
    open_label: Optional[str] = None


class AssignBlockerRequest(BaseModel):
    assigned_to: Optional[str] = None  # user_id or None to unassign


class AddBlockerCommentRequest(BaseModel):
    comment: str


class UpdateBlockerStatusRequest(BaseModel):
    status: str  # 'acknowledged' | 'in_progress' | 'resolved'


class ResolveBlockerRequest(BaseModel):
    unblock_instructions: str


class TeamDetailResponse(BaseModel):
    id: str
    name: str
    plan: str
    member_count: int
    created_at: datetime
    team_type: Optional[str] = None


class UpdateMemberRequest(BaseModel):
    hourly_rate: Optional[float] = None
    timezone: Optional[str] = None
    send_time: Optional[str] = None   # "HH:MM" in member's timezone
    currency: Optional[str] = None
    hours_per_day: Optional[float] = None
    hours_confirmed: Optional[bool] = None


class ConfirmHoursRequest(BaseModel):
    hours_per_day: float

    @field_validator("hours_per_day")
    @classmethod
    def validate_hours(cls, v: float) -> float:
        if v < 0.5 or v > 24:
            raise ValueError("hours_per_day must be between 0.5 and 24")
        return v


class TeamMemberDetailResponse(BaseModel):
    id: str
    user_id: str
    team_id: str
    name: Optional[str]
    email: str
    role: str
    status: str
    checked_in_today: bool
    submitted_at: Optional[datetime]
    created_at: datetime
    hourly_rate: Optional[float] = None
    timezone: Optional[str] = "Asia/Kolkata"
    send_time: Optional[str] = "09:00"
    currency: Optional[str] = "INR"
    hours_per_day: Optional[float] = None
    hours_confirmed: bool = False


class UserTeamsResponse(BaseModel):
    id: str
    name: str
    user_role: str  # 'owner' (manager_id matches) or 'member'
    member_count: int
    plan: str = "free"
    plan_status: str = "active"
    created_at: datetime
    team_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Teams overview / status (T1, T2)
# ---------------------------------------------------------------------------

class TeamsOverviewCheckinsResponse(BaseModel):
    completed: int
    expected: int
    completion_pct: float


class TeamsOverviewSummaryResponse(BaseModel):
    """T1: Aggregate KPIs for the /teams page header (4 cards)."""
    date: date
    active_teams: int
    checkins: TeamsOverviewCheckinsResponse
    active_blockers_total: int
    revenue_at_risk_usd: float


class TeamStatusRowResponse(BaseModel):
    """T2: One row per team for the /teams list page."""
    id: str
    name: str
    plan: str
    user_role: str            # 'owner' | 'member'
    team_type: Optional[str] = None
    member_count: int
    checked_in_count: int
    pending_count: int
    alert_count: int


class TeamsStatusResponse(BaseModel):
    date: date
    teams: List[TeamStatusRowResponse]


# ---------------------------------------------------------------------------
# Team health (T3)
# ---------------------------------------------------------------------------

class CheckinRatePoint(BaseModel):
    date: date
    rate_pct: float


class TeamHealthResponse(BaseModel):
    """T3: 30d (or 7d/90d) health for /teams/{teamid} right panel."""
    range: Literal["7d", "30d", "90d"]
    start_date: date
    end_date: date
    checkin_rate_pct: float
    checkin_rate_delta_pct: float          # percentage points vs the prior equal window
    avg_streak_days: float
    open_blockers: int
    open_blockers_delta_vs_last_week: int
    checkin_rate_series: List[CheckinRatePoint]


# ---------------------------------------------------------------------------
# Nudge suggestion (T4)
# ---------------------------------------------------------------------------

class NudgeAction(BaseModel):
    kind: Literal["send_now"]
    endpoint: str             # frontend hits this to actually send the nudge


class NudgeSuggestionResponse(BaseModel):
    """T4: AI banner copy + target user for /teams/{teamid}.

    The endpoint returns 204 No Content (no response body) when no nudge is
    appropriate (e.g. 100% checked in or no pending members).
    """
    headline: str
    subtitle: str
    target_user_id: str
    target_user_name: str
    target_recent_checkin_rate_pct: float    # member's last-7-day check-in rate
    completion_pct: float                    # team's % checked-in right now
    action: NudgeAction


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

class CreateCheckoutRequest(BaseModel):
    team_id: str


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


# ---------------------------------------------------------------------------
# Automation Radar
# ---------------------------------------------------------------------------

class AutomationRunRequest(BaseModel):
    window_days: Literal[7, 14, 30] = 14


class AutomationAnalysisSummaryResponse(BaseModel):
    id: str
    team_id: str
    window_days: int
    status: str
    period_start: date
    period_end: date
    summary_text: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class AutomationAnalysisDetailResponse(BaseModel):
    id: str
    team_id: str
    window_days: int
    status: str
    period_start: date
    period_end: date
    findings: list         # list of finding dicts returned from LLM
    summary_text: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Ai Task Radar — schedule configuration
# ---------------------------------------------------------------------------

class AutomationScheduleRequest(BaseModel):
    cadence: Literal["weekly", "biweekly", "monthly"] = "weekly"
    day_of_week: int = 0            # 0=Mon ... 6=Sun
    week_of_month: Optional[int] = None   # 1..4 (only when cadence == monthly)
    run_time: str = "08:00"          # HH:MM
    timezone: str = "Asia/Kolkata"
    enabled: bool = True

    @field_validator("day_of_week")
    @classmethod
    def _validate_dow(cls, v: int) -> int:
        if v < 0 or v > 6:
            raise ValueError("day_of_week must be in [0, 6]")
        return v

    @field_validator("week_of_month")
    @classmethod
    def _validate_wom(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 1 or v > 4:
            raise ValueError("week_of_month must be in [1, 4]")
        return v

    @field_validator("run_time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        try:
            hh, mm = v.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except Exception:
            raise ValueError("run_time must be HH:MM")
        return v


class AutomationScheduleResponse(BaseModel):
    team_id: str
    cadence: str
    day_of_week: int
    week_of_month: Optional[int]
    run_time: str
    timezone: str
    enabled: bool
    next_run_at: Optional[datetime]
    last_run_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Ai Task Radar — analysis + tasks
# ---------------------------------------------------------------------------

class AiTaskSuggestionTool(BaseModel):
    name: str
    prompt: Optional[str] = None


class AiTask(BaseModel):
    id: str
    user_id: Optional[str]
    assigned_name: Optional[str]
    task_title: str
    task_description: Optional[str]
    automation_score: int
    tier: str
    mention_frequency: int = 0
    weekly_hours_saved: float = 0.0
    monthly_cost_saved_usd: float = 0.0
    suggested_tools: List[AiTaskSuggestionTool]
    suggested_workflow: Optional[str]
    general_suggestion: Optional[str]


class AiTaskRadarMember(BaseModel):
    user_id: Optional[str]
    name: str
    member_score: int
    task_count: int


class AiTaskRadarAnalysisSummary(BaseModel):
    id: str
    team_id: str
    window_days: int
    status: str
    trigger: str
    period_start: date
    period_end: date
    team_score: Optional[int]
    member_count: Optional[int]
    task_count: Optional[int]
    weekly_hours_saved: Optional[float] = None
    monthly_cost_saved_usd: Optional[float] = None
    high_priority_task_count: Optional[int] = None
    is_empty: bool
    summary_text: Optional[str]
    error_message: Optional[str]
    created_at: datetime


class AiTaskRadarAnalysisDetail(AiTaskRadarAnalysisSummary):
    members: List[AiTaskRadarMember]
    tasks: List[AiTask]


class AiTaskRadarMemberDetail(BaseModel):
    user_id: Optional[str]
    name: str
    member_score: int
    tasks: List[AiTask]


class AiTaskRadarAdminRunRequest(BaseModel):
    """Dev/admin backdoor — only honoured when AI_TASK_RADAR_ADMIN_RUN=1 on the server."""
    window_days: Literal[7, 14, 30] = 7
    trigger: Literal["manual_admin", "initial"] = "manual_admin"


class AiTaskRadarRunRequest(BaseModel):
    window_days: Literal[7, 14, 30] = 7


class AiTaskRadarPlaybookSummary(BaseModel):
    """Backs the top-of-page playbook banner (ATR-1)."""
    analysis_id: str
    tasks_count: int                       # top-N tasks the playbook would cover
    weekly_hours_saved: float
    monthly_cost_saved_usd: float
    payroll_pct: Optional[int]             # cost saved as % of monthly engineering payroll
    generated_available: bool              # false when the latest analysis is empty


class AiTaskRadarInsight(BaseModel):
    id: str
    kind: str          # 'opportunity' | 'blocker' | 'time'
    title: str
    sub: str


class AiTaskRadarTeamPotentialRow(BaseModel):
    team_id: str
    name: str
    blockers: int
    score: int


class AiTaskRadarTrendPoint(BaseModel):
    date: str          # YYYY-MM-DD
    value: int         # team_score 0..100


class AiTaskRadarTrend(BaseModel):
    range: str         # '7d' | '30d' | '90d'
    points: List[AiTaskRadarTrendPoint]


class AiTaskRadarPlaybook(BaseModel):
    analysis_id: str
    filename: str
    markdown: str


class AutomationIntegrationProvider(BaseModel):
    provider: Literal["jira", "linear", "notion", "sheets"]
    status: str   # 'disconnected' | 'coming_soon' | 'pending'
    label: str


class BillingStatusResponse(BaseModel):
    plan: str
    plan_status: str
    ls_subscription_id: Optional[str]
    ls_customer_id: Optional[str]
    plan_expires_at: Optional[datetime] = None
