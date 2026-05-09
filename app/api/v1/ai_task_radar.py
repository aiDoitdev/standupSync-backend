"""
Ai Task Radar router — scheduled, nested team/member/task AI automation analysis.

Endpoints (all require the team's manager; Starter plan only):

  GET   /ai-task-radar/{team_id}/schedule
  PUT   /ai-task-radar/{team_id}/schedule
  GET   /ai-task-radar/{team_id}/history                      — grouped-by-month on the client
  GET   /ai-task-radar/{team_id}/analyses/{analysis_id}       — full team/member/task detail
  GET   /ai-task-radar/{team_id}/analyses/{analysis_id}/members/{member_key}
  POST  /ai-task-radar/{team_id}/run                          — manual trigger (manager, Starter plan)
  POST  /ai-task-radar/{team_id}/admin/run                    — dev backdoor (env-gated)
  GET   /ai-task-radar/{team_id}/integrations                 — stub list (jira/linear/notion)
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_manager, require_team_manager
from app.core.database import get_db
from app.models import (
    AutomationAnalysis,
    AutomationIntegration,
    AutomationSchedule,
    AutomationTask,
    Team,
    User,
)
from app.schemas import (
    AiTask,
    AiTaskRadarAdminRunRequest,
    AiTaskRadarAnalysisDetail,
    AiTaskRadarAnalysisSummary,
    AiTaskRadarMember,
    AiTaskRadarMemberDetail,
    AiTaskRadarRunRequest,
    AiTaskSuggestionTool,
    AutomationIntegrationProvider,
    AutomationScheduleRequest,
    AutomationScheduleResponse,
)
from app.services.ai_task_radar_service import compute_next_run_at, run_team_analysis
from app.core.config import get_settings
from app.utils.plan_limits import require_starter as _require_starter_base

_settings = get_settings()

logger = structlog.get_logger(__name__)
router = APIRouter()


def _require_starter(user: User) -> None:
    _require_starter_base(user, "Ai Task Radar")


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _schedule_to_response(s: AutomationSchedule) -> AutomationScheduleResponse:
    return AutomationScheduleResponse(
        team_id=str(s.team_id),
        cadence=s.cadence,
        day_of_week=s.day_of_week,
        week_of_month=s.week_of_month,
        run_time=s.run_time,
        timezone=s.timezone,
        enabled=s.enabled,
        next_run_at=s.next_run_at,
        last_run_at=s.last_run_at,
    )


def _analysis_to_summary(a: AutomationAnalysis) -> AiTaskRadarAnalysisSummary:
    return AiTaskRadarAnalysisSummary(
        id=str(a.id),
        team_id=str(a.team_id),
        window_days=a.window_days,
        status=a.status,
        trigger=a.trigger or "manual_admin",
        period_start=a.period_start,
        period_end=a.period_end,
        team_score=a.team_score,
        member_count=a.member_count,
        task_count=a.task_count,
        is_empty=bool(a.is_empty),
        summary_text=a.summary_text,
        error_message=a.error_message,
        created_at=a.created_at,
    )


def _task_to_schema(t: AutomationTask) -> AiTask:
    tools_raw: list = []
    if t.suggested_tools_json and isinstance(t.suggested_tools_json, list):
        tools_raw = t.suggested_tools_json
    tools: list[AiTaskSuggestionTool] = []
    for item in tools_raw:
        if isinstance(item, dict) and item.get("name"):
            tools.append(AiTaskSuggestionTool(name=item["name"], prompt=item.get("prompt")))
        elif isinstance(item, str) and item.strip():
            tools.append(AiTaskSuggestionTool(name=item.strip(), prompt=None))
    return AiTask(
        id=str(t.id),
        user_id=str(t.user_id) if t.user_id else None,
        assigned_name=t.assigned_name,
        task_title=t.task_title,
        task_description=t.task_description,
        automation_score=t.automation_score,
        tier=t.tier,
        suggested_tools=tools,
        suggested_workflow=t.suggested_workflow,
        general_suggestion=t.general_suggestion,
    )


def _build_members_rollup(tasks: list[AutomationTask]) -> list[AiTaskRadarMember]:
    """Group tasks by (user_id or assigned_name) and compute member_score = mean(score)."""
    groups: dict[tuple[Optional[str], str], list[AutomationTask]] = {}
    for t in tasks:
        key_uid = str(t.user_id) if t.user_id else None
        key_name = t.assigned_name or "Unknown"
        k = (key_uid, key_name)
        groups.setdefault(k, []).append(t)

    members: list[AiTaskRadarMember] = []
    for (uid, name), ts in groups.items():
        score = int(sum(t.automation_score for t in ts) / len(ts)) if ts else 0
        members.append(AiTaskRadarMember(
            user_id=uid,
            name=name,
            member_score=score,
            task_count=len(ts),
        ))
    members.sort(key=lambda m: (-m.member_score, m.name.lower()))
    return members


# ---------------------------------------------------------------------------
# Schedule — GET / PUT
# ---------------------------------------------------------------------------

@router.get("/{team_id}/schedule", response_model=AutomationScheduleResponse)
async def get_schedule(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    result = await db.execute(select(AutomationSchedule).where(AutomationSchedule.team_id == team.id))
    sched = result.scalar_one_or_none()

    if sched is None:
        # Return a sensible default — unsaved until the manager PUTs.
        return AutomationScheduleResponse(
            team_id=str(team.id),
            cadence="weekly",
            day_of_week=0,
            week_of_month=None,
            run_time="08:00",
            timezone="Asia/Kolkata",
            enabled=False,
            next_run_at=None,
            last_run_at=None,
        )
    return _schedule_to_response(sched)


@router.put("/{team_id}/schedule", response_model=AutomationScheduleResponse)
async def upsert_schedule(
    team_id: str,
    data: AutomationScheduleRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """
    Create or update the Ai Task Radar schedule. On first-ever enable we also trigger
    an immediate analysis so the manager sees results right away.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    if data.cadence == "monthly" and not data.week_of_month:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="week_of_month is required when cadence is 'monthly'",
        )

    result = await db.execute(select(AutomationSchedule).where(AutomationSchedule.team_id == team.id))
    sched = result.scalar_one_or_none()
    first_enable = sched is None or (not sched.enabled and data.enabled)

    if sched is None:
        sched = AutomationSchedule(team_id=team.id)
        db.add(sched)

    sched.cadence = data.cadence
    sched.day_of_week = data.day_of_week
    sched.week_of_month = data.week_of_month
    sched.run_time = data.run_time
    sched.timezone = data.timezone
    sched.enabled = data.enabled

    now_utc = datetime.now(timezone.utc)
    next_run = compute_next_run_at(sched, now_utc=now_utc) if data.enabled else None
    sched.next_run_at = next_run.replace(tzinfo=None) if next_run is not None else None
    await db.commit()
    await db.refresh(sched)

    # Immediate first run on enable — fire-and-forget semantics inside the same request.
    # If the team has no data we still persist the empty marker + email nudge.
    if first_enable and data.enabled:
        try:
            await run_team_analysis(
                db,
                team,
                window_days=7,
                trigger="initial",
                created_by_user_id=current_user.id,
            )
            # Reflect the initial run in last_run_at so biweekly cadence anchors correctly.
            sched.last_run_at = now_utc.replace(tzinfo=None)
            await db.commit()
            await db.refresh(sched)
        except Exception as exc:
            logger.warning("Initial Ai Task Radar run failed for team %s: %s", team.id, exc)

    # Acknowledgment email to team owner — best-effort, never fails the response.
    try:
        from app.services.email_service import send_schedule_config_ack_email
        send_schedule_config_ack_email(
            manager_email=current_user.email,
            manager_name=current_user.name or current_user.email,
            team_name=team.name,
            team_id=str(team.id),
            cadence=sched.cadence,
            day_of_week=sched.day_of_week,
            week_of_month=sched.week_of_month,
            run_time=sched.run_time,
            timezone_str=sched.timezone,
            enabled=sched.enabled,
            next_run_at_utc=sched.next_run_at,
        )
    except Exception as exc:
        logger.warning("Failed to send schedule config ack email for team %s: %s", team.id, exc)

    return _schedule_to_response(sched)


# ---------------------------------------------------------------------------
# History (flat list — frontend groups by month)
# ---------------------------------------------------------------------------

@router.get("/{team_id}/history", response_model=list[AiTaskRadarAnalysisSummary])
async def list_history(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    result = await db.execute(
        select(AutomationAnalysis)
        .where(AutomationAnalysis.team_id == team.id)
        .order_by(AutomationAnalysis.created_at.desc())
        .limit(52)   # ~one year of weekly runs
    )
    rows = result.scalars().all()
    return [_analysis_to_summary(r) for r in rows]


# ---------------------------------------------------------------------------
# Analysis detail — full nested shape for the team overview page
# ---------------------------------------------------------------------------

@router.get("/{team_id}/analyses/{analysis_id}", response_model=AiTaskRadarAnalysisDetail)
async def get_analysis_detail(
    team_id: str,
    analysis_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    analysis_result = await db.execute(
        select(AutomationAnalysis).where(
            and_(
                AutomationAnalysis.id == analysis_id,
                AutomationAnalysis.team_id == team.id,
            )
        )
    )
    analysis = analysis_result.scalar_one_or_none()
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    tasks_result = await db.execute(
        select(AutomationTask)
        .where(AutomationTask.analysis_id == analysis.id)
        .order_by(AutomationTask.automation_score.desc(), AutomationTask.created_at.asc())
    )
    tasks = tasks_result.scalars().all()

    return AiTaskRadarAnalysisDetail(
        **_analysis_to_summary(analysis).model_dump(),
        members=_build_members_rollup(tasks),
        tasks=[_task_to_schema(t) for t in tasks],
    )


# ---------------------------------------------------------------------------
# Per-member detail — same analysis, filtered to one member
# ---------------------------------------------------------------------------

@router.get("/{team_id}/analyses/{analysis_id}/members/{member_key}", response_model=AiTaskRadarMemberDetail)
async def get_member_detail(
    team_id: str,
    analysis_id: str,
    member_key: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """
    member_key is either a user UUID or the fallback assigned_name (URL-encoded) echoed
    back by the LLM when a task couldn't be mapped to a known user.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    analysis_result = await db.execute(
        select(AutomationAnalysis).where(
            and_(
                AutomationAnalysis.id == analysis_id,
                AutomationAnalysis.team_id == team.id,
            )
        )
    )
    if analysis_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    q = select(AutomationTask).where(AutomationTask.analysis_id == analysis_id)
    # Try UUID match first, fall back to name match.
    try:
        import uuid as _uuid
        uid = _uuid.UUID(member_key)
        q = q.where(AutomationTask.user_id == uid)
        display_name = None
    except (ValueError, TypeError):
        q = q.where(AutomationTask.assigned_name == member_key)
        display_name = member_key

    tasks_result = await db.execute(q.order_by(AutomationTask.automation_score.desc()))
    tasks = tasks_result.scalars().all()

    if not tasks:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No tasks for this member in the analysis")

    member_score = int(sum(t.automation_score for t in tasks) / len(tasks))
    name = display_name or (tasks[0].assigned_name or "Unknown")
    user_id = str(tasks[0].user_id) if tasks[0].user_id else None

    return AiTaskRadarMemberDetail(
        user_id=user_id,
        name=name,
        member_score=member_score,
        tasks=[_task_to_schema(t) for t in tasks],
    )


# ---------------------------------------------------------------------------
# Manual trigger — available to any manager on Starter plan
# ---------------------------------------------------------------------------

_CADENCE_WINDOW: dict[str, int] = {"weekly": 7, "biweekly": 14, "monthly": 30}


@router.post("/{team_id}/run", response_model=AiTaskRadarAnalysisSummary, status_code=status.HTTP_201_CREATED)
async def run_analysis_now(
    team_id: str,
    data: AiTaskRadarRunRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate AI Task Radar run outside the schedule."""
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    # Derive window_days from the team's configured cadence when not explicitly provided.
    window_days = data.window_days
    if window_days == 7:
        schedule_row = await db.scalar(
            select(AutomationSchedule).where(AutomationSchedule.team_id == team.id)
        )
        if schedule_row:
            window_days = _CADENCE_WINDOW.get(schedule_row.cadence, 7)

    record = await run_team_analysis(
        db,
        team,
        window_days=window_days,
        trigger="manual",
        created_by_user_id=current_user.id,
    )
    return _analysis_to_summary(record)


# ---------------------------------------------------------------------------
# Admin backdoor — dev-only
# ---------------------------------------------------------------------------

@router.post("/{team_id}/admin/run", response_model=AiTaskRadarAnalysisSummary, status_code=status.HTTP_201_CREATED)
async def admin_run_analysis(
    team_id: str,
    data: AiTaskRadarAdminRunRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin/dev backdoor to trigger an immediate Ai Task Radar run outside the schedule.
    Only enabled when AI_TASK_RADAR_ADMIN_RUN=1 is set on the server.
    """
    if not _settings.ai_task_radar_admin_run:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin run is disabled. Set AI_TASK_RADAR_ADMIN_RUN=1 to enable.",
        )
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    record = await run_team_analysis(
        db,
        team,
        window_days=data.window_days,
        trigger=data.trigger,
        created_by_user_id=current_user.id,
    )
    return _analysis_to_summary(record)


# ---------------------------------------------------------------------------
# Integrations stub
# ---------------------------------------------------------------------------

_PROVIDER_LABELS = {"jira": "Jira", "linear": "Linear", "notion": "Notion", "sheets": "Sheets"}


@router.get("/{team_id}/integrations", response_model=list[AutomationIntegrationProvider])
async def list_integrations(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """List integration providers with current status. Always returns the full 3 for the
    stub panel — unconfigured providers are reported as 'coming_soon'."""
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    rows_result = await db.execute(
        select(AutomationIntegration).where(AutomationIntegration.team_id == team.id)
    )
    existing = {r.provider: r.status for r in rows_result.scalars().all()}

    out: list[AutomationIntegrationProvider] = []
    for provider, label in _PROVIDER_LABELS.items():
        out.append(AutomationIntegrationProvider(
            provider=provider,   # type: ignore[arg-type]
            status=existing.get(provider, "coming_soon"),
            label=label,
        ))
    return out
