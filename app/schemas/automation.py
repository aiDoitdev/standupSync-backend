from datetime import date, datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, field_validator


# ── Automation Radar (legacy) ─────────────────────────────────────────────────

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
    findings: list
    summary_text: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


# ── AI Task Radar — schedule ──────────────────────────────────────────────────

class AutomationScheduleRequest(BaseModel):
    cadence: Literal["weekly", "biweekly", "monthly"] = "weekly"
    day_of_week: int = 0
    week_of_month: Optional[int] = None
    run_time: str = "08:00"
    timezone: str = "Asia/Kolkata"
    enabled: bool = True

    @field_validator("day_of_week")
    @classmethod
    def _validate_dow(cls, v: int) -> int:
        if not 0 <= v <= 6:
            raise ValueError("day_of_week must be in [0, 6]")
        return v

    @field_validator("week_of_month")
    @classmethod
    def _validate_wom(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not 1 <= v <= 4:
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


# ── AI Task Radar — analysis + tasks ─────────────────────────────────────────

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
    window_days: Literal[7, 14, 30] = 7
    trigger: Literal["manual_admin", "initial"] = "manual_admin"


class AutomationIntegrationProvider(BaseModel):
    provider: Literal["jira", "linear", "notion", "sheets"]
    status: str
    label: str
