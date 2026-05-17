"""Manager dashboard — account-level aggregates across every team the
authenticated user owns or belongs to.

These endpoints intentionally aggregate over the whole account (not a single
team) and are NOT Starter-plan gated, unlike the per-team Blocker-Intelligence
endpoints. See Dashboard_Backend_API_Requirements.md §1–§6.
"""
import structlog
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.team import Team, TeamMember
from app.models.user import User
from app.models.checkin import Checkin
from app.models.blocker import Blocker
from app.models.automation import AutomationAnalysis
from app.utils.currency import member_hourly_usd, fmt_duration
from app.schemas.dashboard import (
    DashboardTeamStatus,
    DashboardTeamsStatusResponse,
    DashboardTopBlocker,
    DashboardTopBlockersResponse,
    RevenueAtRisk,
    ActiveBlockers,
    AutomationSavings,
    CheckinRate,
    DashboardSummaryResponse,
    RevenueLossPoint,
    RevenueLossSeriesResponse,
    DashboardInsight,
    DashboardInsightsResponse,
)

_TOP_N_TASKS = 5
_CHECKIN_WINDOW_DAYS = 30
_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}

_OPEN_STATUSES = ("open", "acknowledged", "in_progress")

# Keyword → icon kind. The Blocker model has no category column, so kind is
# inferred from the title (the frontend renders code/doc/box and defaults the
# rest to the doc icon). See Dashboard_Backend_API_Requirements.md §5.
_KIND_KEYWORDS = [
    ("infra",  ("ci/cd", "pipeline", "deploy", "infrastructure", "server", "kubernetes", "docker", "aws", "cloud")),
    ("bug",    ("bug", "error", "crash", "fails", "failing", "broken", "regression")),
    ("code",   ("code", "merge", "branch", "api", "build", "compile", "refactor")),
    ("design", ("design", "ui", "ux", "figma", "mockup", "layout")),
    ("people", ("onboarding", "hire", "training", "team", "manager", "review")),
    ("doc",    ("doc", "documentation", "report", "notes", "spec", "wiki")),
    ("box",    ("release", "package", "dependency", "vendor", "third-party", "license")),
]


def _infer_kind(title: str) -> str:
    t = (title or "").lower()
    for kind, words in _KIND_KEYWORDS:
        if any(w in t for w in words):
            return kind
    return "doc"


async def _owned_teams(current_user: User, db: AsyncSession) -> list[Team]:
    """Teams the user manages (manager-owned). The manager dashboard's
    revenue/blocker aggregates are scoped to owned teams."""
    if current_user.role != "manager":
        return []
    return list(
        (await db.execute(select(Team).where(Team.manager_id == current_user.id)))
        .scalars()
        .all()
    )

logger = structlog.get_logger(__name__)
router = APIRouter()


async def _accessible_teams(current_user: User, db: AsyncSession) -> list[tuple[Team, str, str]]:
    """Return [(team, user_role, plan)] for every team the user owns or has
    joined — mirrors GET /teams/ scoping so the dashboard sees the same set.
    """
    out: list[tuple[Team, str, str]] = []

    if current_user.role == "manager":
        managed = (
            await db.execute(select(Team).where(Team.manager_id == current_user.id))
        ).scalars().all()
        for team in managed:
            out.append((team, "owner", current_user.plan))

    member_rows = (
        await db.execute(
            select(Team, User)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .join(User, Team.manager_id == User.id)
            .where(
                and_(
                    TeamMember.user_id == current_user.id,
                    TeamMember.status == "active",
                )
            )
        )
    ).all()
    for team, manager in member_rows:
        out.append((team, "member", manager.plan))

    return out


@router.get("/teams-status", response_model=DashboardTeamsStatusResponse)
async def teams_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Today's check-in status for every team in one round trip.

    Replaces the frontend's 1 + N (`/teams/` then per-team `/teams/{id}/members`)
    fan-out. `total_count`/`member_count` count all team_member rows for parity
    with the existing member view; `submitted_count` is members who submitted a
    check-in today.
    """
    teams = await _accessible_teams(current_user, db)
    team_ids = [t.id for t, _, _ in teams]

    member_counts: dict = defaultdict(int)
    submitted_counts: dict = defaultdict(int)

    if team_ids:
        mc_rows = (
            await db.execute(
                select(TeamMember.team_id, func.count(TeamMember.id))
                .where(TeamMember.team_id.in_(team_ids))
                .group_by(TeamMember.team_id)
            )
        ).all()
        member_counts = {tid: cnt for tid, cnt in mc_rows}

        today = date.today()
        sc_rows = (
            await db.execute(
                select(Checkin.team_id, func.count(Checkin.id))
                .where(
                    and_(
                        Checkin.team_id.in_(team_ids),
                        Checkin.date == today,
                        Checkin.submitted_at.isnot(None),
                    )
                )
                .group_by(Checkin.team_id)
            )
        ).all()
        submitted_counts = {tid: cnt for tid, cnt in sc_rows}

    items = []
    for team, user_role, plan in teams:
        total = int(member_counts.get(team.id, 0))
        submitted = int(submitted_counts.get(team.id, 0))
        items.append(
            DashboardTeamStatus(
                id=str(team.id),
                name=team.name,
                plan=plan or "free",
                user_role=user_role,
                member_count=total,
                submitted_count=submitted,
                total_count=total,
            )
        )

    # Sort by completion ratio desc, then name — most-attention-needed last.
    items.sort(
        key=lambda t: (
            -(t.submitted_count / t.total_count) if t.total_count else 1.0,
            t.name.lower(),
        )
    )

    return DashboardTeamsStatusResponse(teams=items)


@router.get("/top-blockers", response_model=DashboardTopBlockersResponse)
async def top_blockers(
    limit: int = Query(5, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Account-wide open blockers ranked by dollars at risk.

    amount_usd = (open_seconds / 3600) × assignee_hourly_rate_usd, matching
    the per-team computation in /reports/{team_id}/blocked-cost. Rows whose
    assignee has no configured rate get amount_usd = 0 and sort to the bottom.
    """
    teams = await _owned_teams(current_user, db)
    if not teams:
        return DashboardTopBlockersResponse(blockers=[])

    team_ids = [t.id for t in teams]

    blockers = list(
        (
            await db.execute(
                select(Blocker).where(
                    and_(
                        Blocker.team_id.in_(team_ids),
                        Blocker.status.in_(_OPEN_STATUSES),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if not blockers:
        return DashboardTopBlockersResponse(blockers=[])

    # Rate + name lookups for every (team, member) and user referenced.
    tm_rows = (
        await db.execute(
            select(TeamMember).where(TeamMember.team_id.in_(team_ids))
        )
    ).scalars().all()
    rate_map: dict = {
        (tm.team_id, tm.user_id): (tm.hourly_rate, tm.currency) for tm in tm_rows
    }

    user_ids = {b.assigned_to or b.user_id for b in blockers}
    name_rows = (
        await db.execute(select(User.id, User.name, User.email).where(User.id.in_(user_ids)))
    ).all()
    name_map = {uid: (name or email) for uid, name, email in name_rows}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows: list[DashboardTopBlocker] = []
    for b in blockers:
        owner_id = b.assigned_to or b.user_id
        open_seconds = max(0, int((now - b.created_at).total_seconds()))
        hourly = rate_map.get((b.team_id, owner_id))
        hourly_usd = member_hourly_usd(*hourly) if hourly else None
        amount = round((open_seconds / 3600) * hourly_usd, 2) if hourly_usd else 0.0
        rows.append(
            DashboardTopBlocker(
                id=str(b.id),
                team_id=str(b.team_id),
                title=b.title,
                kind=_infer_kind(b.title),
                owner_name=name_map.get(owner_id, "Unassigned"),
                owner_id=str(owner_id) if owner_id else None,
                age_label=fmt_duration(open_seconds / 3600),
                open_seconds=open_seconds,
                amount_usd=amount,
                status=b.status,
            )
        )

    rows.sort(key=lambda r: r.amount_usd, reverse=True)
    return DashboardTopBlockersResponse(blockers=rows[:limit])


async def _rate_map(team_ids: list, db: AsyncSession) -> dict:
    """(team_id, user_id) -> (hourly_rate, currency) for every team member."""
    tm_rows = (
        await db.execute(select(TeamMember).where(TeamMember.team_id.in_(team_ids)))
    ).scalars().all()
    return {(tm.team_id, tm.user_id): (tm.hourly_rate, tm.currency) for tm in tm_rows}


@router.get("/summary", response_model=DashboardSummaryResponse)
async def summary(
    range: str = Query("30d", pattern="^(7d|30d|90d)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Account-wide KPI strip for the manager dashboard (§1 + §6).

    Revenue/burn-rate are point-in-time over currently-open blockers; the
    blocker delta is fixed at 7 days and the check-in rate is a fixed 30-day
    rolling window with a previous-30-day delta (the `range` param is accepted
    for API compatibility but the documented KPIs use these fixed windows).
    Automation $ / hours are null until AI Task Radar emits cost metrics —
    only the real recurring-task count is returned. See §6.
    """
    teams = await _owned_teams(current_user, db)
    if not teams:
        return DashboardSummaryResponse(
            revenue_at_risk=RevenueAtRisk(
                amount_usd=0.0, burn_rate_per_hour_usd=0.0,
                burn_rate_per_day_usd=0.0, burn_rate_per_week_usd=0.0,
            ),
            active_blockers=ActiveBlockers(count=0, delta_vs_last_week=0),
            automation_savings=AutomationSavings(
                potential_monthly_usd=None, hours_per_week=None,
                top_n_tasks=_TOP_N_TASKS, task_count=0,
            ),
            checkin_rate=CheckinRate(rate_pct_30d=0.0, delta_pct_vs_prev_30d=0.0),
        )

    team_ids = [t.id for t in teams]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # ── Revenue at risk + burn rate (currently-open blockers) ────────────────
    open_blockers = list(
        (
            await db.execute(
                select(Blocker).where(
                    and_(Blocker.team_id.in_(team_ids), Blocker.status.in_(_OPEN_STATUSES))
                )
            )
        ).scalars().all()
    )
    rate_map = await _rate_map(team_ids, db)

    amount_usd = 0.0
    burn_per_hour = 0.0
    for b in open_blockers:
        owner_id = b.assigned_to or b.user_id
        hourly = rate_map.get((b.team_id, owner_id))
        hourly_usd = member_hourly_usd(*hourly) if hourly else None
        if not hourly_usd:
            continue
        open_hours = max(0.0, (now - b.created_at).total_seconds() / 3600)
        amount_usd += open_hours * hourly_usd
        burn_per_hour += hourly_usd

    burn_per_hour = round(burn_per_hour, 2)
    revenue = RevenueAtRisk(
        amount_usd=round(amount_usd, 2),
        burn_rate_per_hour_usd=burn_per_hour,
        burn_rate_per_day_usd=round(burn_per_hour * 24, 2),
        burn_rate_per_week_usd=round(burn_per_hour * 24 * 7, 2),
    )

    # ── Active blockers + 7-day delta ────────────────────────────────────────
    open_count = len(open_blockers)
    cutoff_7d = now - timedelta(days=7)
    open_7d_ago = (
        await db.execute(
            select(func.count(Blocker.id)).where(
                and_(
                    Blocker.team_id.in_(team_ids),
                    Blocker.created_at <= cutoff_7d,
                    or_(Blocker.resolved_at.is_(None), Blocker.resolved_at > cutoff_7d),
                )
            )
        )
    ).scalar() or 0
    active = ActiveBlockers(count=open_count, delta_vs_last_week=open_count - int(open_7d_ago))

    # ── Check-in rate: rolling 30d vs previous 30d ───────────────────────────
    active_members = (
        await db.execute(
            select(func.count(TeamMember.id)).where(
                and_(TeamMember.team_id.in_(team_ids), TeamMember.status == "active")
            )
        )
    ).scalar() or 0

    today = date.today()
    cur_start = today - timedelta(days=_CHECKIN_WINDOW_DAYS - 1)
    prev_start = cur_start - timedelta(days=_CHECKIN_WINDOW_DAYS)
    prev_end = cur_start - timedelta(days=1)

    async def _submitted(d0: date, d1: date) -> int:
        return int(
            (
                await db.execute(
                    select(func.count(Checkin.id)).where(
                        and_(
                            Checkin.team_id.in_(team_ids),
                            Checkin.submitted_at.isnot(None),
                            Checkin.date >= d0,
                            Checkin.date <= d1,
                        )
                    )
                )
            ).scalar()
            or 0
        )

    expected = int(active_members) * _CHECKIN_WINDOW_DAYS
    if expected > 0:
        cur_rate = round(await _submitted(cur_start, today) / expected * 100, 1)
        prev_rate = round(await _submitted(prev_start, prev_end) / expected * 100, 1)
    else:
        cur_rate = prev_rate = 0.0
    checkin = CheckinRate(
        rate_pct_30d=cur_rate,
        delta_pct_vs_prev_30d=round(cur_rate - prev_rate, 1),
    )

    # ── Automation savings — real task_count only (honest empty state) ───────
    task_count = 0
    for tid in team_ids:
        latest = (
            await db.execute(
                select(AutomationAnalysis)
                .where(
                    and_(
                        AutomationAnalysis.team_id == tid,
                        AutomationAnalysis.status == "completed",
                    )
                )
                .order_by(AutomationAnalysis.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest and latest.task_count:
            task_count += int(latest.task_count)

    automation = AutomationSavings(
        potential_monthly_usd=None,
        hours_per_week=None,
        top_n_tasks=_TOP_N_TASKS,
        task_count=task_count,
    )

    return DashboardSummaryResponse(
        revenue_at_risk=revenue,
        active_blockers=active,
        automation_savings=automation,
        checkin_rate=checkin,
    )


@router.get("/revenue-loss-series", response_model=RevenueLossSeriesResponse)
async def revenue_loss_series(
    range_: str = Query("30d", alias="range", pattern="^(7d|30d|90d)$"),
    cumulative: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Account-wide daily revenue lost to open blockers (§2).

    For each day, value = Σ over blockers open during that day of
    (hours_open_within_day × assignee_hourly_rate_usd). With cumulative=true
    each point carries the running total. Same cost model as /dashboard/summary
    and /reports blocked-cost.
    """
    days = _RANGE_DAYS[range_]
    today = date.today()
    start = today - timedelta(days=days - 1)
    win_start = datetime.combine(start, datetime.min.time())
    win_end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    teams = await _owned_teams(current_user, db)
    points: list[RevenueLossPoint] = []

    if teams:
        team_ids = [t.id for t in teams]
        blockers = list(
            (
                await db.execute(
                    select(Blocker).where(
                        and_(
                            Blocker.team_id.in_(team_ids),
                            Blocker.created_at < win_end,
                            or_(
                                Blocker.resolved_at.is_(None),
                                Blocker.resolved_at >= win_start,
                            ),
                        )
                    )
                )
            ).scalars().all()
        )
        rate_map = await _rate_map(team_ids, db)

        priced = []
        for b in blockers:
            owner_id = b.assigned_to or b.user_id
            hourly = rate_map.get((b.team_id, owner_id))
            hourly_usd = member_hourly_usd(*hourly) if hourly else None
            if hourly_usd:
                end = b.resolved_at if b.resolved_at else now
                priced.append((b.created_at, min(end, now), hourly_usd))

        running = 0.0
        for i in range(days):
            d = start + timedelta(days=i)
            day_start = datetime.combine(d, datetime.min.time())
            day_end = day_start + timedelta(days=1)
            day_val = 0.0
            for c_at, b_end, rate in priced:
                ov_start = max(c_at, day_start)
                ov_end = min(b_end, day_end)
                if ov_end > ov_start:
                    day_val += (ov_end - ov_start).total_seconds() / 3600 * rate
            running += day_val
            points.append(
                RevenueLossPoint(
                    date=d.isoformat(),
                    value=round(running if cumulative else day_val, 2),
                )
            )
    else:
        for i in range(days):
            points.append(
                RevenueLossPoint(date=(start + timedelta(days=i)).isoformat(), value=0.0)
            )

    return RevenueLossSeriesResponse(range=range_, currency="USD", points=points)


@router.get("/ai-insights", response_model=DashboardInsightsResponse)
async def ai_insights(
    limit: int = Query(3, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Prioritised, account-wide insight cards derived from real data (§3).

    Deterministically built from open blockers, their dollar impact, staleness
    and the latest AI Task Radar analyses — no fabricated copy. `kind` ∈
    warning|automation|stale|trend (the frontend defaults unknown kinds to the
    warning icon).
    """
    teams = await _owned_teams(current_user, db)
    if not teams:
        return DashboardInsightsResponse(insights=[])

    team_ids = [t.id for t in teams]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    open_blockers = list(
        (
            await db.execute(
                select(Blocker).where(
                    and_(Blocker.team_id.in_(team_ids), Blocker.status.in_(_OPEN_STATUSES))
                )
            )
        ).scalars().all()
    )
    rate_map = await _rate_map(team_ids, db)
    owner_ids = {b.assigned_to or b.user_id for b in open_blockers}
    name_rows = (
        await db.execute(select(User.id, User.name, User.email).where(User.id.in_(owner_ids)))
    ).all() if owner_ids else []
    name_map = {uid: (name or email) for uid, name, email in name_rows}

    priced = []
    for b in open_blockers:
        owner_id = b.assigned_to or b.user_id
        hourly = rate_map.get((b.team_id, owner_id))
        hourly_usd = member_hourly_usd(*hourly) if hourly else None
        open_hours = max(0.0, (now - b.created_at).total_seconds() / 3600)
        priced.append((b, owner_id, hourly_usd, open_hours))

    insights: list[DashboardInsight] = []

    # 1. Highest-cost open blocker (warning).
    with_cost = [p for p in priced if p[2]]
    if with_cost:
        b, owner_id, hourly_usd, open_hours = max(
            with_cost, key=lambda p: p[3] * p[2]
        )
        weekly = hourly_usd * 24 * 7
        insights.append(
            DashboardInsight(
                id=f"ins_blocker_{b.id}",
                kind="warning",
                title=f"{b.title[:60]} costing ${weekly:,.0f} / week",
                subtitle=f"Open {fmt_duration(open_hours)} — owned by {name_map.get(owner_id, 'unassigned')}.",
                severity="high",
                deep_link=f"/blockers?team_id={b.team_id}&id={b.id}",
                related_blocker_ids=[str(b.id)],
                related_user_ids=[str(owner_id)] if owner_id else [],
            )
        )

    # 2. Stale blockers open > 7 days.
    stale = [p for p in priced if p[3] > 24 * 7]
    if stale:
        stale_names = []
        for _, oid, _, _ in stale:
            nm = name_map.get(oid)
            if nm and nm not in stale_names:
                stale_names.append(nm)
        who = ", ".join(stale_names[:3]) if stale_names else "various owners"
        insights.append(
            DashboardInsight(
                id="ins_stale",
                kind="stale",
                title=f"{len(stale)} blocker{'s' if len(stale) != 1 else ''} stale > 7 days",
                subtitle=f"{who} — assign owners or escalate.",
                severity="medium",
                deep_link="/blockers?status=stale",
                related_blocker_ids=[str(p[0].id) for p in stale],
            )
        )

    # 3. Recurring automation candidates (real task_count, honest — no $).
    task_count = 0
    for tid in team_ids:
        latest = (
            await db.execute(
                select(AutomationAnalysis)
                .where(
                    and_(
                        AutomationAnalysis.team_id == tid,
                        AutomationAnalysis.status == "completed",
                    )
                )
                .order_by(AutomationAnalysis.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest and latest.task_count:
            task_count += int(latest.task_count)
    if task_count > 0:
        insights.append(
            DashboardInsight(
                id="ins_automation",
                kind="automation",
                title=f"{task_count} recurring task{'s' if task_count != 1 else ''} detected",
                subtitle="AI Task Radar flagged these as automation candidates.",
                severity="medium",
                deep_link="/ai-radar",
            )
        )

    # 4. Check-in rate trend (30d vs prev 30d).
    active_members = (
        await db.execute(
            select(func.count(TeamMember.id)).where(
                and_(TeamMember.team_id.in_(team_ids), TeamMember.status == "active")
            )
        )
    ).scalar() or 0
    if active_members:
        today = date.today()
        cur_start = today - timedelta(days=_CHECKIN_WINDOW_DAYS - 1)
        prev_start = cur_start - timedelta(days=_CHECKIN_WINDOW_DAYS)
        prev_end = cur_start - timedelta(days=1)

        async def _sub(d0, d1):
            return int(
                (
                    await db.execute(
                        select(func.count(Checkin.id)).where(
                            and_(
                                Checkin.team_id.in_(team_ids),
                                Checkin.submitted_at.isnot(None),
                                Checkin.date >= d0,
                                Checkin.date <= d1,
                            )
                        )
                    )
                ).scalar()
                or 0
            )

        exp = int(active_members) * _CHECKIN_WINDOW_DAYS
        cur = round(await _sub(cur_start, today) / exp * 100, 1)
        prev = round(await _sub(prev_start, prev_end) / exp * 100, 1)
        delta = round(cur - prev, 1)
        if abs(delta) >= 1.0:
            up = delta >= 0
            insights.append(
                DashboardInsight(
                    id="ins_trend",
                    kind="trend",
                    title=f"Check-in rate {'up' if up else 'down'} {abs(delta)} pp (30d)",
                    subtitle=f"Now {cur}% vs {prev}% the previous 30 days.",
                    severity="low" if up else "medium",
                    deep_link="/reports",
                )
            )

    return DashboardInsightsResponse(insights=insights[:limit])
