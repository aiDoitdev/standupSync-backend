from typing import List, Optional
from pydantic import BaseModel


class DashboardTeamStatus(BaseModel):
    id: str
    name: str
    plan: str
    user_role: str          # "owner" | "member"
    member_count: int
    submitted_count: int
    total_count: int


class DashboardTeamsStatusResponse(BaseModel):
    teams: List[DashboardTeamStatus]


class DashboardTopBlocker(BaseModel):
    id: str
    team_id: str
    title: str
    kind: str               # code|doc|box|bug|infra|design|people
    owner_name: str
    owner_id: Optional[str]
    age_label: str          # e.g. "18d 4h"
    open_seconds: int
    amount_usd: float
    status: str


class DashboardTopBlockersResponse(BaseModel):
    blockers: List[DashboardTopBlocker]


class RevenueAtRisk(BaseModel):
    amount_usd: float
    burn_rate_per_hour_usd: float
    burn_rate_per_day_usd: float
    burn_rate_per_week_usd: float


class ActiveBlockers(BaseModel):
    count: int
    delta_vs_last_week: int


class AutomationSavings(BaseModel):
    potential_monthly_usd: Optional[float]   # null until AI Task Radar emits $ metrics
    hours_per_week: Optional[float]          # null until AI Task Radar emits hour metrics
    top_n_tasks: int
    task_count: int                          # §6 — recurring tasks detected (real)


class CheckinRate(BaseModel):
    rate_pct_30d: float
    delta_pct_vs_prev_30d: float


class DashboardSummaryResponse(BaseModel):
    revenue_at_risk: RevenueAtRisk
    active_blockers: ActiveBlockers
    automation_savings: AutomationSavings
    checkin_rate: CheckinRate


class RevenueLossPoint(BaseModel):
    date: str               # YYYY-MM-DD
    value: float


class RevenueLossSeriesResponse(BaseModel):
    range: str
    currency: str
    points: List[RevenueLossPoint]


class DashboardInsight(BaseModel):
    id: str
    kind: str               # warning | automation | stale | trend
    title: str
    subtitle: str
    severity: str           # high | medium | low
    deep_link: str
    related_blocker_ids: List[str] = []
    related_user_ids: List[str] = []
    related_task_ids: List[str] = []


class DashboardInsightsResponse(BaseModel):
    insights: List[DashboardInsight]
