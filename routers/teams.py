import csv
import io
import logging
import uuid
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from database import get_db
from models import (
    Team,
    TeamMember,
    TeamQuestion,
    User,
    Invite,
    Checkin,
    CheckinAnswer,
    Blocker,
)
from schemas import (
    CreateTeamRequest,
    TeamResponse,
    TeamDetailResponse,
    UserTeamsResponse,
    InviteMembersRequest,
    MemberStatusResponse,
    TeamMemberDetailResponse,
    TeamQuestionResponse,
    TeamQuestionCreateRequest,
    TeamQuestionUpdateRequest,
    UpdateMemberRequest,
    PendingInviteResponse,
    ConfirmHoursRequest,
    TeamsOverviewSummaryResponse,
    TeamsOverviewCheckinsResponse,
    TeamsStatusResponse,
    TeamStatusRowResponse,
    TeamHealthResponse,
    CheckinRatePoint,
    NudgeSuggestionResponse,
    NudgeAction,
)
from auth import (
    get_current_user,
    require_manager,
    require_team_access,
    require_team_manager,
)
from email_service import send_invite_email
from plan_limits import FREE_MEMBER_LIMIT, team_has_starter_access, require_starter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/create", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: CreateTeamRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Create a new team. Free plan: 1 team. Starter plan: unlimited teams."""
    # Check if user already has a team on the free plan
    existing_teams = await db.execute(
        select(Team).where(Team.manager_id == current_user.id)
    )
    user_teams = existing_teams.scalars().all()

    if user_teams:
        has_starter = team_has_starter_access(current_user)
        if not has_starter:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Free plan allows only 1 team. Upgrade any team to Starter ($19/mo) for unlimited teams.",
            )

    team = Team(
        name=data.name,
        manager_id=current_user.id,
        team_type=data.team_type,
    )
    db.add(team)
    await db.flush()  # get team.id before creating questions

    # Create 3 default standup questions for every new team
    default_questions = [
        TeamQuestion(team_id=team.id, order_index=0, label="What did you accomplish yesterday?", enabled=True, is_blocker_type=False, canonical_kind="yesterday"),
        TeamQuestion(team_id=team.id, order_index=1, label="What will you work on today?", enabled=True, is_blocker_type=False, canonical_kind="today"),
        TeamQuestion(team_id=team.id, order_index=2, label="Any blockers or issues?", enabled=True, is_blocker_type=True, canonical_kind="blockers"),
    ]
    for q in default_questions:
        db.add(q)

    await db.commit()
    await db.refresh(team)

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=0,
    )


@router.get("/", response_model=list[UserTeamsResponse])
async def get_all_teams(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all teams where user is manager or active member."""
    teams_list = []

    # Get teams user manages
    if current_user.role == "manager":
        result = await db.execute(select(Team).where(Team.manager_id == current_user.id))
        managed_teams = result.scalars().all()

        for team in managed_teams:
            count_result = await db.execute(
                select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
            )
            member_count = count_result.scalar() or 0

            teams_list.append(
                UserTeamsResponse(
                    id=str(team.id),
                    name=team.name,
                    user_role="owner",
                    member_count=member_count,
                    plan=current_user.plan or "free",
                    plan_status=current_user.plan_status or "active",
                    created_at=team.created_at,
                    team_type=team.team_type,
                )
            )

    # Get teams user is a member of
    result = await db.execute(
        select(TeamMember, Team)
        .join(Team, TeamMember.team_id == Team.id)
        .where(
            and_(
                TeamMember.user_id == current_user.id,
                TeamMember.status == "active",
            )
        )
    )
    member_teams = result.all()

    for tm, team in member_teams:
        count_result = await db.execute(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
        )
        member_count = count_result.scalar() or 0

        mgr_result = await db.execute(select(User).where(User.id == team.manager_id))
        mgr = mgr_result.scalar_one_or_none()

        teams_list.append(
            UserTeamsResponse(
                id=str(team.id),
                name=team.name,
                user_role="member",
                member_count=member_count,
                plan=mgr.plan if mgr else "free",
                plan_status=mgr.plan_status if mgr else "active",
                created_at=team.created_at,
                team_type=team.team_type,
            )
        )

    return teams_list


# ---------------------------------------------------------------------------
# Legacy endpoints (kept BEFORE /{team_id} to prevent route shadowing)
# ---------------------------------------------------------------------------

# Legacy endpoint for backward compatibility
@router.get("/my", response_model=TeamResponse, deprecated=True)
async def get_my_team(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Get the first team for manager. Use GET / instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
    )


# Legacy endpoint for backward compatibility
@router.get("/members", response_model=list[MemberStatusResponse], deprecated=True)
async def list_members_legacy(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: List members of first team. Use GET /{team_id}/members instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    today = date.today()
    rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
    )
    members = rows.all()

    response = []
    for tm, user in members:
        checkin_result = await db.execute(
            select(Checkin).where(
                and_(
                    Checkin.team_id == team.id,
                    Checkin.user_id == user.id,
                    Checkin.date == today,
                )
            )
        )
        checkin = checkin_result.scalars().first()

        response.append(
            MemberStatusResponse(
                user_id=str(user.id),
                name=user.name,
                email=user.email,
                member_status=tm.status,
                checked_in_today=bool(checkin and checkin.submitted_at),
                submitted_at=checkin.submitted_at if checkin else None,
            )
        )

    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ensure_team_questions(team: Team, db: AsyncSession) -> None:
    """Lazily migrate legacy q1/q2/q3 labels to TeamQuestion rows if none exist yet."""
    try:
        existing = await db.execute(
            select(TeamQuestion).where(TeamQuestion.team_id == team.id).limit(1)
        )
        if existing.scalar_one_or_none():
            return  # questions already exist

        seeds = [
            TeamQuestion(team_id=team.id, order_index=0, label=team.q1_label or "What did you accomplish yesterday?", enabled=True, is_blocker_type=False, canonical_kind="yesterday"),
            TeamQuestion(team_id=team.id, order_index=1, label=team.q2_label or "What will you work on today?", enabled=True, is_blocker_type=False, canonical_kind="today"),
            TeamQuestion(team_id=team.id, order_index=2, label=team.q3_label or "Any blockers or issues?", enabled=True, is_blocker_type=True, canonical_kind="blockers"),
        ]
        for q in seeds:
            db.add(q)
        await db.commit()
    except Exception:
        logger.exception("Failed to seed default questions for team %s — continuing without seeding", team.id)
        await db.rollback()


# ---------------------------------------------------------------------------
# Dashboard helpers (T1 / T2 / T3) — currency conversion + scope resolution
# ---------------------------------------------------------------------------

# USD-base conversion table. Mirrors reports._RATES_TO_USD; duplicated rather
# than imported to avoid cross-router coupling — both copies need to move
# together to a shared module if more endpoints adopt it.
_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "INR": 0.012,
    "EUR": 1.08,
    "GBP": 1.27,
    "AED": 0.27,
    "SGD": 0.74,
    "CAD": 0.74,
    "AUD": 0.65,
}


_OPEN_BLOCKER_STATUSES = ("open", "acknowledged", "in_progress")
_OVERDUE_BLOCKER_DAYS = 7   # alert_count = members owning a blocker open ≥ this many days


def _hourly_rate_usd(member: TeamMember) -> float:
    """Convert a member's hourly_rate (in their currency) to USD. 0 if unset."""
    if member is None or member.hourly_rate is None:
        return 0.0
    return float(member.hourly_rate) * _RATES_TO_USD.get((member.currency or "INR").upper(), _RATES_TO_USD["INR"])


def _parse_optional_iso_date(value: Optional[str], *, field: str = "date") -> date:
    """Parse a YYYY-MM-DD query param. Defaults to today if absent."""
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field} format. Expected YYYY-MM-DD.",
        )


async def _accessible_teams(current_user: User, db: AsyncSession) -> list[tuple[Team, str]]:
    """Resolve every team the caller can see, paired with their role on that team.

    Returns a list of (team, user_role) tuples where user_role is 'owner' or 'member'.
    Mirrors the scope of `GET /teams/` so the dashboard counters never disagree
    with the table on the same page.
    """
    teams: list[tuple[Team, str]] = []
    seen: set = set()

    if current_user.role == "manager":
        managed = await db.execute(select(Team).where(Team.manager_id == current_user.id))
        for team in managed.scalars().all():
            teams.append((team, "owner"))
            seen.add(team.id)

    member_rows = await db.execute(
        select(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            and_(
                TeamMember.user_id == current_user.id,
                TeamMember.status == "active",
            )
        )
    )
    for team in member_rows.scalars().all():
        if team.id in seen:
            continue   # owner role takes precedence
        teams.append((team, "member"))
        seen.add(team.id)

    return teams


# ---------------------------------------------------------------------------
# T1 — Teams overview summary (KPI cards on /teams)
# ---------------------------------------------------------------------------

@router.get("/overview-summary", response_model=TeamsOverviewSummaryResponse)
async def teams_overview_summary(
    date_param: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD; defaults to today"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate KPIs for the /teams page header.

    Scope: every team the caller owns OR is an active member of (mirrors `GET /teams/`).
    `?date=` lets the header date-pill drill into historical days; defaults to today.
    """
    target_date = _parse_optional_iso_date(date_param, field="date")
    accessible = await _accessible_teams(current_user, db)
    team_ids = [t.id for t, _ in accessible]

    if not team_ids:
        return TeamsOverviewSummaryResponse(
            date=target_date,
            active_teams=0,
            checkins=TeamsOverviewCheckinsResponse(completed=0, expected=0, completion_pct=0.0),
            active_blockers_total=0,
            revenue_at_risk_usd=0.0,
        )

    # Expected today = sum of active members across visible teams.
    expected_result = await db.execute(
        select(func.count(TeamMember.id)).where(
            and_(
                TeamMember.team_id.in_(team_ids),
                TeamMember.status == "active",
            )
        )
    )
    expected = int(expected_result.scalar() or 0)

    # Completed = submitted check-ins on `target_date` across visible teams.
    completed_result = await db.execute(
        select(func.count(Checkin.id)).where(
            and_(
                Checkin.team_id.in_(team_ids),
                Checkin.date == target_date,
                Checkin.submitted_at.isnot(None),
            )
        )
    )
    completed = int(completed_result.scalar() or 0)

    completion_pct = round(completed / expected * 100, 1) if expected > 0 else 0.0

    # Active blockers across visible teams.
    active_blockers_result = await db.execute(
        select(func.count(Blocker.id)).where(
            and_(
                Blocker.team_id.in_(team_ids),
                Blocker.status.in_(_OPEN_BLOCKER_STATUSES),
            )
        )
    )
    active_blockers_total = int(active_blockers_result.scalar() or 0)

    # Revenue at risk = sum over open blockers of (hours_open × assignee_hourly_rate_usd).
    # Reuse member_hourly_rate; if unset, the blocker contributes $0.
    open_blockers_rows = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id.in_(team_ids),
                Blocker.status.in_(_OPEN_BLOCKER_STATUSES),
            )
        )
    )
    open_blockers = open_blockers_rows.scalars().all()

    # Build a (team_id, user_id) -> TeamMember map for rate lookup.
    member_lookup: dict[tuple, TeamMember] = {}
    if open_blockers:
        member_rows = await db.execute(
            select(TeamMember).where(
                and_(
                    TeamMember.team_id.in_(team_ids),
                    TeamMember.status == "active",
                )
            )
        )
        for tm in member_rows.scalars().all():
            member_lookup[(tm.team_id, tm.user_id)] = tm

    now = datetime.utcnow()
    revenue_at_risk_usd = 0.0
    for b in open_blockers:
        # Cost is attributed to the assignee (preferred) or the reporter (fallback).
        owner_id = b.assigned_to or b.user_id
        member = member_lookup.get((b.team_id, owner_id))
        rate_usd = _hourly_rate_usd(member)
        if rate_usd <= 0:
            continue
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        revenue_at_risk_usd += rate_usd * hours_open

    return TeamsOverviewSummaryResponse(
        date=target_date,
        active_teams=len(accessible),
        checkins=TeamsOverviewCheckinsResponse(
            completed=completed,
            expected=expected,
            completion_pct=completion_pct,
        ),
        active_blockers_total=active_blockers_total,
        revenue_at_risk_usd=round(revenue_at_risk_usd, 2),
    )


# ---------------------------------------------------------------------------
# T2 — Per-team status row (replaces N+1 calls on /teams list page)
# ---------------------------------------------------------------------------

@router.get("/teams-status", response_model=TeamsStatusResponse)
async def teams_status(
    date_param: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD; defaults to today"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-team status counters for the /teams list table.

    Replaces the 1 + 2N round-trip fan-out (`/teams/{id}/members` × N +
    `/checkin/{id}/today` × N) that the page does today. Scope mirrors
    `GET /teams/`.
    """
    target_date = _parse_optional_iso_date(date_param, field="date")
    accessible = await _accessible_teams(current_user, db)
    if not accessible:
        return TeamsStatusResponse(date=target_date, teams=[])

    team_ids = [t.id for t, _ in accessible]
    role_by_team_id: dict = {t.id: role for t, role in accessible}

    # Member counts (one row per team).
    member_count_rows = await db.execute(
        select(TeamMember.team_id, func.count(TeamMember.id))
        .where(TeamMember.team_id.in_(team_ids))
        .group_by(TeamMember.team_id)
    )
    member_count_by_team: dict = {row[0]: int(row[1]) for row in member_count_rows.all()}

    # Check-ins on target_date (one row per team).
    checkin_count_rows = await db.execute(
        select(Checkin.team_id, func.count(Checkin.id))
        .where(
            and_(
                Checkin.team_id.in_(team_ids),
                Checkin.date == target_date,
                Checkin.submitted_at.isnot(None),
            )
        )
        .group_by(Checkin.team_id)
    )
    checkin_count_by_team: dict = {row[0]: int(row[1]) for row in checkin_count_rows.all()}

    # alert_count = unique members on the team owning ≥1 open blocker that's
    # been open for ≥ _OVERDUE_BLOCKER_DAYS. "Owner" = assigned_to (preferred)
    # or reporter.
    overdue_cutoff = datetime.utcnow() - timedelta(days=_OVERDUE_BLOCKER_DAYS)
    overdue_rows = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id.in_(team_ids),
                Blocker.status.in_(_OPEN_BLOCKER_STATUSES),
                Blocker.created_at <= overdue_cutoff,
            )
        )
    )
    alert_members: dict = defaultdict(set)
    for b in overdue_rows.scalars().all():
        owner_id = b.assigned_to or b.user_id
        if owner_id is None:
            continue
        alert_members[b.team_id].add(owner_id)

    # Resolve each team's manager once for plan/plan_status lookup.
    manager_ids = list({t.manager_id for t, _ in accessible if t.manager_id})
    manager_rows = await db.execute(select(User).where(User.id.in_(manager_ids))) if manager_ids else None
    manager_by_id: dict = {}
    if manager_rows is not None:
        for u in manager_rows.scalars().all():
            manager_by_id[u.id] = u

    rows: list[TeamStatusRowResponse] = []
    for team, role in accessible:
        member_count = member_count_by_team.get(team.id, 0)
        checked_in_count = checkin_count_by_team.get(team.id, 0)
        # Pending can never go negative — if a member checked in then was
        # removed mid-day, checked_in_count > member_count is possible.
        pending_count = max(0, member_count - checked_in_count)
        alert_count = len(alert_members.get(team.id, set()))

        manager = manager_by_id.get(team.manager_id)
        plan = (manager.plan if manager else "free") or "free"

        rows.append(
            TeamStatusRowResponse(
                id=str(team.id),
                name=team.name,
                plan=plan,
                user_role=role,
                team_type=team.team_type,
                member_count=member_count,
                checked_in_count=checked_in_count,
                pending_count=pending_count,
                alert_count=alert_count,
            )
        )

    # Sort by completion percentage descending (matches the doc's "completion desc"
    # suggestion); ties broken by recency so newest teams appear first.
    def _completion(row: TeamStatusRowResponse) -> float:
        return row.checked_in_count / row.member_count if row.member_count > 0 else 0.0
    created_at_by_team: dict = {t.id: t.created_at for t, _ in accessible}
    rows.sort(
        key=lambda r: (
            -_completion(r),
            -(created_at_by_team.get(uuid.UUID(r.id)) or datetime.min).timestamp(),
        )
    )

    return TeamsStatusResponse(date=target_date, teams=rows)


# ---------------------------------------------------------------------------
# Team CRUD (parameterized routes — must be after static legacy routes above)
# ---------------------------------------------------------------------------

@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team_details(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get team details. User must be manager or active member."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Lazy-migrate: if this team has no TeamQuestion rows yet, seed from legacy q*_label columns
    await _ensure_team_questions(team, db)

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    mgr_result = await db.execute(select(User).where(User.id == team.manager_id))
    mgr = mgr_result.scalar_one_or_none()

    return TeamDetailResponse(
        id=str(team.id),
        name=team.name,
        plan=mgr.plan if mgr else "free",
        member_count=member_count,
        created_at=team.created_at,
        team_type=team.team_type,
    )


@router.put("/{team_id}", response_model=TeamDetailResponse)
async def update_team(
    team_id: str,
    data: CreateTeamRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update team name and type. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    team.name = data.name
    team.team_type = data.team_type
    db.add(team)
    await db.commit()
    await db.refresh(team)

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamDetailResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
        created_at=team.created_at,
        team_type=team.team_type,
    )


# ---------------------------------------------------------------------------
# Team Questions CRUD (Issue 4: configurable standup questions)
# ---------------------------------------------------------------------------

@router.get("/{team_id}/questions", response_model=list[TeamQuestionResponse])
async def list_questions(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all questions for a team, ordered by order_index."""
    team, _ = await require_team_access(team_id, current_user, db)
    await _ensure_team_questions(team, db)

    result = await db.execute(
        select(TeamQuestion)
        .where(TeamQuestion.team_id == team.id)
        .order_by(TeamQuestion.order_index)
    )
    return [
        TeamQuestionResponse(
            id=str(q.id), team_id=str(q.team_id), order_index=q.order_index,
            label=q.label, enabled=q.enabled, is_blocker_type=q.is_blocker_type,
            canonical_kind=q.canonical_kind,
            created_at=q.created_at,
        )
        for q in result.scalars().all()
    ]


@router.post("/{team_id}/questions", response_model=TeamQuestionResponse, status_code=status.HTTP_201_CREATED)
async def add_question(
    team_id: str,
    data: TeamQuestionCreateRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Add a new question to the team. Manager only. Requires Starter plan."""
    team, _ = await require_team_manager(team_id, current_user, db)
    require_starter(current_user, "Custom standup questions")

    # Determine next order_index
    max_result = await db.execute(
        select(func.max(TeamQuestion.order_index)).where(TeamQuestion.team_id == team.id)
    )
    max_idx = max_result.scalar() or -1

    question = TeamQuestion(
        team_id=team.id,
        order_index=max_idx + 1,
        label=data.label,
        enabled=data.enabled,
        is_blocker_type=data.is_blocker_type,
        canonical_kind=data.canonical_kind,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    return TeamQuestionResponse(
        id=str(question.id), team_id=str(question.team_id), order_index=question.order_index,
        label=question.label, enabled=question.enabled, is_blocker_type=question.is_blocker_type,
        canonical_kind=question.canonical_kind,
        created_at=question.created_at,
    )


@router.put("/{team_id}/questions/{question_id}", response_model=TeamQuestionResponse)
async def update_question(
    team_id: str,
    question_id: str,
    data: TeamQuestionUpdateRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update a question's label, enabled status, blocker flag, or order. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamQuestion).where(
            and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    if data.label is not None:
        question.label = data.label
    if data.enabled is not None:
        question.enabled = data.enabled
    if data.is_blocker_type is not None:
        question.is_blocker_type = data.is_blocker_type
    if data.order_index is not None:
        question.order_index = data.order_index
    if "canonical_kind" in data.model_fields_set:
        question.canonical_kind = data.canonical_kind

    db.add(question)
    await db.commit()
    await db.refresh(question)

    return TeamQuestionResponse(
        id=str(question.id), team_id=str(question.team_id), order_index=question.order_index,
        label=question.label, enabled=question.enabled, is_blocker_type=question.is_blocker_type,
        canonical_kind=question.canonical_kind,
        created_at=question.created_at,
    )


@router.delete("/{team_id}/questions/{question_id}", status_code=status.HTTP_200_OK)
async def delete_question(
    team_id: str,
    question_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Delete a question. At least 1 question must remain. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    count_result = await db.execute(
        select(func.count(TeamQuestion.id)).where(
            and_(TeamQuestion.team_id == team.id, TeamQuestion.enabled == True)
        )
    )
    enabled_count = count_result.scalar() or 0

    result = await db.execute(
        select(TeamQuestion).where(
            and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    if enabled_count <= 1 and question.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one question must remain enabled.",
        )

    await db.delete(question)
    await db.commit()
    return {"message": "Question deleted"}


@router.post("/{team_id}/invite", status_code=status.HTTP_200_OK)
async def invite_members(
    team_id: str,
    data: InviteMembersRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Invite members to a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    # Enforce free-plan member limit (active members + pending invites)
    if not team_has_starter_access(current_user):
        count_result = await db.execute(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
        )
        member_count = count_result.scalar() or 0
        pending_result = await db.execute(
            select(func.count(Invite.id)).where(
                and_(Invite.team_id == team.id, Invite.used == False)
            )
        )
        pending_count = pending_result.scalar() or 0
        total = member_count + pending_count
        if total + len(data.emails) > FREE_MEMBER_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": (
                        f"Free plan allows {FREE_MEMBER_LIMIT} invited member per team. "
                        f"You currently have {total}. "
                        "Upgrade to Starter ($19/mo) for unlimited members and teams."
                    ),
                    "upgrade_required": True,
                    "current_count": total,
                    "limit": FREE_MEMBER_LIMIT,
                },
            )

    sent = 0
    failed = []
    skipped = []

    for email in data.emails:
        # Skip if a pending invite already exists
        existing_invite = await db.execute(
            select(Invite).where(
                and_(
                    Invite.team_id == team.id,
                    Invite.email == email,
                    Invite.used == False,
                )
            )
        )
        if existing_invite.scalar_one_or_none():
            skipped.append({"email": email, "reason": "pending invite already exists"})
            continue

        # Skip if the email already belongs to an active team member
        existing_member = await db.execute(
            select(TeamMember)
            .join(User, TeamMember.user_id == User.id)
            .where(
                and_(
                    TeamMember.team_id == team.id,
                    User.email == email,
                    TeamMember.status == "active",
                )
            )
        )
        if existing_member.scalar_one_or_none():
            skipped.append({"email": email, "reason": "already a team member"})
            continue

        token = str(uuid.uuid4())
        invite = Invite(team_id=team.id, email=email, token=token)
        db.add(invite)
        try:
            send_invite_email(to_email=email, team_name=team.name, invite_token=token)
            sent += 1
        except Exception as e:
            logger.error("Failed to send invite to %s: %s", email, e)
            failed.append({"email": email, "error": str(e)})
            await db.delete(invite)

    await db.commit()

    if failed and sent == 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "All invite emails failed to send. Check your Resend domain verification.",
                "failures": failed,
            },
        )

    return {
        "message": f"Invites sent to {sent} member(s).",
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
    }


@router.get("/{team_id}/pending-invites", response_model=list[PendingInviteResponse])
async def list_pending_invites(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """List pending (unused, non-expired) invites for a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    now = datetime.utcnow()
    result = await db.execute(
        select(Invite).where(
            and_(
                Invite.team_id == team.id,
                Invite.used == False,
                Invite.expires_at > now,
            )
        ).order_by(Invite.created_at)
    )
    invites = result.scalars().all()
    return [
        PendingInviteResponse(
            id=str(inv.id),
            email=inv.email,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in invites
    ]


@router.delete("/{team_id}/pending-invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    team_id: str,
    invite_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (delete) a pending invite. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(Invite).where(
            and_(
                Invite.id == invite_id,
                Invite.team_id == team.id,
                Invite.used == False,
            )
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    await db.delete(invite)
    await db.commit()


@router.get("/{team_id}/members", response_model=list[TeamMemberDetailResponse])
async def list_team_members(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a team. User must be manager or member."""
    team, _ = await require_team_access(team_id, current_user, db)
    today = date.today()

    rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    )
    members = rows.all()

    result = []
    for tm, user in members:
        # Check today's checkin status
        checkin_result = await db.execute(
            select(Checkin).where(
                and_(
                    Checkin.team_id == team.id,
                    Checkin.user_id == user.id,
                    Checkin.date == today,
                )
            )
        )
        checkin = checkin_result.scalars().first()

        result.append(
            TeamMemberDetailResponse(
                id=str(tm.id),
                user_id=str(user.id),
                team_id=str(team.id),
                name=user.name,
                email=user.email,
                role=tm.role,
                status=tm.status,
                checked_in_today=bool(checkin and checkin.submitted_at),
                submitted_at=checkin.submitted_at if checkin else None,
                created_at=tm.created_at,
                hourly_rate=tm.hourly_rate,
                timezone=tm.timezone or "Asia/Kolkata",
                send_time=tm.send_time or "09:00",
                currency=tm.currency or "INR",
                hours_per_day=tm.hours_per_day,
                hours_confirmed=tm.hours_confirmed or False,
            )
        )

    return result


@router.put("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def update_member(
    team_id: str,
    user_id: str,
    data: UpdateMemberRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update member settings (e.g. hourly_rate). Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    if data.hourly_rate is not None:
        member.hourly_rate = data.hourly_rate
    elif "hourly_rate" in data.model_fields_set:
        member.hourly_rate = None
    if data.timezone is not None:
        member.timezone = data.timezone
    if data.send_time is not None:
        member.send_time = data.send_time
    if data.currency is not None:
        member.currency = data.currency
    if data.hours_per_day is not None:
        member.hours_per_day = data.hours_per_day
    elif "hours_per_day" in data.model_fields_set:
        member.hours_per_day = None
    if data.hours_confirmed is not None:
        member.hours_confirmed = data.hours_confirmed
    db.add(member)
    await db.commit()

    return {"message": "Member updated successfully"}


@router.delete("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def remove_member(
    team_id: str,
    user_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    await db.delete(member)
    await db.commit()

    return {"message": "Member removed successfully"}


@router.post("/{team_id}/co-manager", status_code=status.HTTP_200_OK)
async def add_co_manager(
    team_id: str,
    data: dict,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Add a co-manager to a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    email = data.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required",
        )

    # Find user with this email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email {email} not found",
        )

    # Check if already a member
    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user.id)
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.role = "co-manager"
        if existing.status == "pending":
            existing.status = "active"
        db.add(existing)
    else:
        member = TeamMember(
            team_id=team.id,
            user_id=user.id,
            role="co-manager",
            status="active",
        )
        db.add(member)

    await db.commit()

    return {"message": f"{email} is now a co-manager of {team.name}"}


# Legacy endpoint for backward compatibility (GET /my and GET /members moved to top, before /{team_id})
@router.post("/invite", status_code=status.HTTP_200_OK, deprecated=True)
async def invite_members_legacy_post(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Get the first team for manager. Use GET / instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
    )


async def invite_members_legacy_post(
    data: InviteMembersRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Invite members to first team. Use POST /{team_id}/invite instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    return await invite_members(str(team.id), data, current_user, db)


# ---------------------------------------------------------------------------
# T3 — Team health (powers /teams/{teamid} right panel)
# ---------------------------------------------------------------------------

_RANGE_TO_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5   # Mon=0..Fri=4


def _compute_member_streak(submitted_dates: set, today: date) -> int:
    """Current consecutive-day streak ending today (or yesterday).

    Mirrors `reports._compute_streak`. Duplicated so this router doesn't
    import from another router; if a third caller appears, hoist to a util.
    """
    cursor = today
    streak = 0
    while str(cursor) in submitted_dates:
        streak += 1
        cursor -= timedelta(days=1)
    if streak == 0:
        cursor = today - timedelta(days=1)
        while str(cursor) in submitted_dates:
            streak += 1
            cursor -= timedelta(days=1)
    return streak


@router.get("/{team_id}/health", response_model=TeamHealthResponse)
async def team_health(
    team_id: str,
    range_param: str = Query("30d", alias="range", description="One of 7d | 30d | 90d"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Team health for the /teams/{teamid} right panel.

    All numbers are computed against active members and weekday-only expected
    days so the rate is comparable across teams that don't run weekend stand-ups.
    """
    team, _ = await require_team_access(team_id, current_user, db)

    if range_param not in _RANGE_TO_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid range. Must be one of: {list(_RANGE_TO_DAYS.keys())}",
        )
    days = _RANGE_TO_DAYS[range_param]
    today = date.today()
    start = today - timedelta(days=days - 1)
    prior_start = start - timedelta(days=days)
    prior_end = start - timedelta(days=1)

    # Active members — define expected check-ins.
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()
    active_member_count = len(members)

    # Pull every relevant check-in for the team across the prior+current window
    # in one query, then bucket by date in Python — avoids two round trips.
    checkins_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= prior_start,
                Checkin.date <= today,
            )
        )
    )
    checkins = checkins_result.scalars().all()

    submitted_dates_by_user: dict[uuid.UUID, set] = defaultdict(set)
    submitted_per_day: dict[date, int] = defaultdict(int)
    for c in checkins:
        submitted_per_day[c.date] += 1
        submitted_dates_by_user[c.user_id].add(str(c.date))

    weekdays_in_range = sum(1 for i in range_days(start, today) if _is_weekday(i))
    weekdays_in_prior = sum(1 for i in range_days(prior_start, prior_end) if _is_weekday(i))

    expected_current = active_member_count * weekdays_in_range
    expected_prior = active_member_count * weekdays_in_prior

    submitted_current = sum(
        submitted_per_day[d] for d in range_days(start, today) if _is_weekday(d)
    )
    submitted_prior = sum(
        submitted_per_day[d] for d in range_days(prior_start, prior_end) if _is_weekday(d)
    )

    rate_current = round(submitted_current / expected_current * 100, 1) if expected_current > 0 else 0.0
    rate_prior = round(submitted_prior / expected_prior * 100, 1) if expected_prior > 0 else 0.0
    rate_delta = round(rate_current - rate_prior, 1)

    # Average per-member current streak.
    if active_member_count > 0:
        streaks = [
            _compute_member_streak(submitted_dates_by_user.get(user.id, set()), today)
            for _, user in members
        ]
        avg_streak = round(sum(streaks) / len(streaks), 1)
    else:
        avg_streak = 0.0

    # Open blockers — current count and count from 7 days ago for delta.
    all_blockers_result = await db.execute(
        select(Blocker).where(Blocker.team_id == team.id)
    )
    all_blockers = all_blockers_result.scalars().all()

    open_now = sum(1 for b in all_blockers if b.status in _OPEN_BLOCKER_STATUSES)
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    open_seven_days_ago = 0
    for b in all_blockers:
        if b.created_at > seven_days_ago:
            continue
        if b.resolved_at and b.resolved_at <= seven_days_ago:
            continue
        open_seven_days_ago += 1

    # Sparkline series — weekdays only, one point per day.
    series: list[CheckinRatePoint] = []
    for d in range_days(start, today):
        if not _is_weekday(d):
            continue
        if active_member_count <= 0:
            series.append(CheckinRatePoint(date=d, rate_pct=0.0))
            continue
        rate = round(submitted_per_day.get(d, 0) / active_member_count * 100, 1)
        series.append(CheckinRatePoint(date=d, rate_pct=rate))

    return TeamHealthResponse(
        range=range_param,
        start_date=start,
        end_date=today,
        checkin_rate_pct=rate_current,
        checkin_rate_delta_pct=rate_delta,
        avg_streak_days=avg_streak,
        open_blockers=open_now,
        open_blockers_delta_vs_last_week=open_now - open_seven_days_ago,
        checkin_rate_series=series,
    )


def range_days(start: date, end: date):
    """Yield each date in [start, end] inclusive."""
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


# ---------------------------------------------------------------------------
# T4 — Nudge suggestion banner copy + target
# ---------------------------------------------------------------------------

def _format_clock(now: datetime) -> str:
    """12-hour clock label for the banner headline (e.g. '9:30 AM').

    Backend uses UTC for `now`. The banner copy is roughly time-of-day style;
    if a future spec needs the team's timezone, plumb it through TeamMember.
    """
    hour_12 = now.hour % 12 or 12
    suffix = "AM" if now.hour < 12 else "PM"
    return f"{hour_12}:{now.minute:02d} {suffix}"


@router.get("/{team_id}/nudge-suggestion", response_model=NudgeSuggestionResponse)
async def nudge_suggestion(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Suggest who to nudge for the team-detail page banner.

    Picks the pending member with the lowest 7-day check-in rate as the nudge
    target, and returns templated prose. Returns 204 No Content when nothing
    to nudge (no pending members or the team has no members yet).
    """
    team, _ = await require_team_access(team_id, current_user, db)
    today = date.today()

    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()
    if not members:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    today_checkins_result = await db.execute(
        select(Checkin.user_id).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.date == today,
                Checkin.submitted_at.isnot(None),
            )
        )
    )
    checked_in_user_ids = {row[0] for row in today_checkins_result.all()}

    pending = [(tm, user) for tm, user in members if user.id not in checked_in_user_ids]
    if not pending:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    member_count = len(members)
    completion_pct = round(
        (member_count - len(pending)) / member_count * 100, 1
    ) if member_count > 0 else 0.0

    # 7-day check-in rate for each pending member, weekdays only.
    seven_days_ago = today - timedelta(days=6)
    pending_user_ids = [user.id for _, user in pending]
    history_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.user_id.in_(pending_user_ids),
                Checkin.submitted_at.isnot(None),
                Checkin.date >= seven_days_ago,
                Checkin.date <= today,
            )
        )
    )
    submitted_by_user: dict[uuid.UUID, int] = defaultdict(int)
    for c in history_result.scalars().all():
        if _is_weekday(c.date):
            submitted_by_user[c.user_id] += 1

    weekdays = sum(1 for d in range_days(seven_days_ago, today) if _is_weekday(d))

    def member_rate(user_id) -> float:
        if weekdays <= 0:
            return 0.0
        return round(submitted_by_user.get(user_id, 0) / weekdays * 100, 1)

    pending.sort(key=lambda pair: (member_rate(pair[1].id), pair[1].name or pair[1].email))
    target_tm, target_user = pending[0]
    target_name = target_user.name or target_user.email
    target_first_name = target_name.split()[0] if target_name else target_name
    target_rate = member_rate(target_user.id)

    headline = f"Great momentum — {completion_pct:g}% checked in by {_format_clock(datetime.utcnow())}"
    subtitle = (
        f"{target_first_name} hasn't checked in yet. Last 7 days they're at "
        f"{target_rate:g}% — consider a quick nudge to keep their streak."
    )

    return NudgeSuggestionResponse(
        headline=headline,
        subtitle=subtitle,
        target_user_id=str(target_user.id),
        target_user_name=target_name,
        target_recent_checkin_rate_pct=target_rate,
        completion_pct=completion_pct,
        action=NudgeAction(
            kind="send_now",
            endpoint=f"/checkin/{team.id}/send-now/{target_user.id}",
        ),
    )


# ---------------------------------------------------------------------------
# T6 — Per-team-per-day check-ins export
# ---------------------------------------------------------------------------

@router.get("/{team_id}/checkins/export")
async def export_team_checkins(
    team_id: str,
    date_param: Optional[str] = Query(None, alias="date", description="YYYY-MM-DD; defaults to today"),
    format: str = Query("csv", description="csv | json"),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Export a single day's check-ins for a team as CSV or JSON.

    One row per (member, question). Members who didn't check in still appear
    with empty answers so managers can see who skipped at a glance.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    target_date = _parse_optional_iso_date(date_param, field="date")
    fmt = format.lower()
    if fmt not in ("csv", "json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid format. Must be one of: csv, json",
        )

    # Make sure the team has TeamQuestion rows (lazy-migrate from legacy q*_label).
    await _ensure_team_questions(team, db)

    questions_result = await db.execute(
        select(TeamQuestion)
        .where(TeamQuestion.team_id == team.id)
        .order_by(TeamQuestion.order_index)
    )
    questions = questions_result.scalars().all()

    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    )
    members = members_result.all()

    checkins_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.date == target_date,
            )
        )
    )
    checkin_by_user: dict[uuid.UUID, Checkin] = {
        c.user_id: c for c in checkins_result.scalars().all()
    }

    checkin_ids = [c.id for c in checkin_by_user.values()]
    answer_map: dict[tuple, str] = {}   # (checkin_id, question_id) -> answer
    if checkin_ids:
        answers_result = await db.execute(
            select(CheckinAnswer).where(CheckinAnswer.checkin_id.in_(checkin_ids))
        )
        for a in answers_result.scalars().all():
            answer_map[(a.checkin_id, a.question_id)] = a.answer

    def _legacy_answer(checkin: Checkin, q: TeamQuestion) -> str:
        """Fall back to Checkin.yesterday/today/blockers for old check-ins."""
        kind = q.canonical_kind
        if kind == "yesterday":
            return checkin.yesterday or ""
        if kind == "today":
            return checkin.today or ""
        if kind == "blockers":
            return checkin.blockers or ""
        return ""

    rows: list[dict] = []
    for tm, user in members:
        checkin = checkin_by_user.get(user.id)
        submitted_at = checkin.submitted_at.isoformat() if (checkin and checkin.submitted_at) else None
        for q in questions:
            answer = ""
            if checkin is not None:
                answer = answer_map.get((checkin.id, q.id), "") or _legacy_answer(checkin, q)
            rows.append({
                "team_id": str(team.id),
                "team_name": team.name,
                "date": str(target_date),
                "user_id": str(user.id),
                "member_name": user.name or "",
                "member_email": user.email,
                "member_role": tm.role,
                "member_status": tm.status,
                "submitted": bool(checkin and checkin.submitted_at),
                "submitted_at": submitted_at,
                "question_id": str(q.id),
                "question_label": q.label,
                "question_kind": q.canonical_kind or "",
                "question_order": q.order_index,
                "answer": answer,
            })

    filename_base = f"team-{team.id}-checkins-{target_date}"

    if fmt == "json":
        return {
            "team_id": str(team.id),
            "team_name": team.name,
            "date": str(target_date),
            "rows": rows,
        }

    # CSV streaming response — keeps memory bounded for big teams.
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "team_id", "team_name", "date",
            "user_id", "member_name", "member_email", "member_role", "member_status",
            "submitted", "submitted_at",
            "question_id", "question_label", "question_kind", "question_order", "answer",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.csv"'},
    )
