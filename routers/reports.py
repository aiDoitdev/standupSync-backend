import logging
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from database import get_db
from models import Team, TeamMember, User, Checkin, Blocker
from auth import get_current_user, require_manager, require_team_access, require_team_manager
from plan_limits import require_starter as _require_starter_base

logger = logging.getLogger(__name__)
router = APIRouter()

REPORTS_DAYS = 30
MAX_REPORTS_DAYS = 365


def _parse_date_range(start_date: Optional[str], end_date: Optional[str]) -> tuple:
    """Parse and validate date range. Returns (start_d, end_d, period_days)."""
    today = date.today()
    end_d = date.fromisoformat(end_date) if end_date else today
    end_d = min(end_d, today)
    start_d = date.fromisoformat(start_date) if start_date else end_d - timedelta(days=REPORTS_DAYS - 1)
    if (end_d - start_d).days > MAX_REPORTS_DAYS:
        start_d = end_d - timedelta(days=MAX_REPORTS_DAYS)
    if start_d > end_d:
        start_d = end_d
    period_days = (end_d - start_d).days + 1
    return start_d, end_d, period_days


def _require_starter(manager: User) -> None:
    _require_starter_base(manager, "Reports")


def _week_monday(d: date) -> str:
    """Return the ISO date string of the Monday of the week containing `d`."""
    return str(d - timedelta(days=d.weekday()))


def _compute_streak(submitted_dates: set, today: date) -> int:
    """Return current consecutive-day streak ending today (or yesterday)."""
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


# ─── Member: personal stats ───────────────────────────────────────────────────

@router.get("/my-stats")
async def my_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return personal check-in analytics for the currently logged-in user."""
    since, today, period_days = _parse_date_range(start_date, end_date)

    result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.user_id == current_user.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= since,
                Checkin.date <= today,
            )
        ).order_by(Checkin.date.asc())
    )
    checkins = result.scalars().all()
    submitted_dates = {str(c.date) for c in checkins}

    # Build day-by-day list
    daily = []
    cursor = since
    while cursor <= today:
        daily.append({"date": str(cursor), "submitted": str(cursor) in submitted_dates})
        cursor += timedelta(days=1)

    # Current streak
    current_streak = _compute_streak(submitted_dates, date.today())

    # Longest streak in the window
    longest = cur_run = 0
    for day in daily:
        if day["submitted"]:
            cur_run += 1
            longest = max(longest, cur_run)
        else:
            cur_run = 0

    # Submission hour distribution
    time_dist: dict[int, int] = defaultdict(int)
    for c in checkins:
        if c.submitted_at:
            time_dist[c.submitted_at.hour] += 1

    # Average submission time
    avg_time = None
    submitted_with_time = [c for c in checkins if c.submitted_at]
    if submitted_with_time:
        total_mins = sum(c.submitted_at.hour * 60 + c.submitted_at.minute for c in submitted_with_time)
        avg_minutes = total_mins // len(submitted_with_time)
        avg_time = f"{avg_minutes // 60:02d}:{avg_minutes % 60:02d}"

    return {
        "period_days": period_days,
        "start_date": str(since),
        "end_date": str(today),
        "total_checkins": len(checkins),
        "checkin_rate": round(len(checkins) / period_days * 100, 1),
        "current_streak": current_streak,
        "longest_streak": longest,
        "daily_checkins": daily,
        "avg_submission_time": avg_time,
        "submission_times": [
            {"hour": h, "count": time_dist[h]}
            for h in range(24)
            if time_dist.get(h, 0) > 0
        ],
    }


# ─── Manager: team-level analytics ───────────────────────────────────────────

@router.get("/{team_id}/summary")
async def team_summary(
    team_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return team-level analytics. Requires Starter plan."""
    team, _ = await require_team_access(team_id, current_user, db)
    mgr_result = await db.execute(select(User).where(User.id == team.manager_id))
    team_manager = mgr_result.scalar_one()
    _require_starter(team_manager)

    since, today, period_days = _parse_date_range(start_date, end_date)

    # ── Active members ──────────────────────────────────────────────────────
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()
    total_members = len(members)

    # ── Checkins in selected window ─────────────────────────────────────────
    checkins_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= since,
                Checkin.date <= today,
            )
        ).order_by(Checkin.date.asc())
    )
    checkins = checkins_result.scalars().all()

    # ── Daily check-in rates ────────────────────────────────────────────────
    daily_count: dict[str, int] = defaultdict(int)
    for c in checkins:
        daily_count[str(c.date)] += 1

    daily_rates = []
    cursor = since
    while cursor <= today:
        key = str(cursor)
        submitted = daily_count.get(key, 0)
        daily_rates.append({
            "date": key,
            "submitted": submitted,
            "total": total_members,
            "rate": round(submitted / total_members * 100, 1) if total_members else 0,
        })
        cursor += timedelta(days=1)

    # ── Per-member stats (leaderboard) ──────────────────────────────────────
    member_checkin_map: dict[str, list] = defaultdict(list)
    for c in checkins:
        member_checkin_map[str(c.user_id)].append(c)

    member_stats = []
    for tm, user in members:
        uid = str(user.id)
        user_checkins = member_checkin_map.get(uid, [])
        count = len(user_checkins)

        submitted_dates = {str(c.date) for c in user_checkins}
        streak = _compute_streak(submitted_dates, date.today())

        times = [c.submitted_at for c in user_checkins if c.submitted_at]
        avg_time = None
        if times:
            total_mins = sum(t.hour * 60 + t.minute for t in times)
            avg_minutes = total_mins // len(times)
            avg_time = f"{avg_minutes // 60:02d}:{avg_minutes % 60:02d}"

        member_stats.append({
            "user_id": uid,
            "name": user.name or user.email,
            "checkins": count,
            "rate": round(count / period_days * 100, 1),
            "streak": streak,
            "avg_time": avg_time,
        })

    member_stats.sort(key=lambda x: x["checkins"], reverse=True)

    # ── Submission time distribution ────────────────────────────────────────
    time_dist: dict[int, int] = defaultdict(int)
    for c in checkins:
        if c.submitted_at:
            time_dist[c.submitted_at.hour] += 1

    # ── Blocker trends (weekly groups within date range) ────────────────────
    blockers_result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.created_at >= datetime.combine(since, datetime.min.time()),
                Blocker.created_at < datetime.combine(today + timedelta(days=1), datetime.min.time()),
            )
        )
    )
    blockers = blockers_result.scalars().all()

    weekly_opened: dict[str, int] = defaultdict(int)
    weekly_resolved: dict[str, int] = defaultdict(int)
    for b in blockers:
        ws = _week_monday(b.created_at.date())
        weekly_opened[ws] += 1
        if b.resolved_at:
            ws_res = _week_monday(b.resolved_at.date())
            weekly_resolved[ws_res] += 1

    blocker_trends = []
    week_cursor = since - timedelta(days=since.weekday())  # Monday of first week
    seen_weeks: set[str] = set()
    while week_cursor <= today:
        ws = str(week_cursor)
        if ws not in seen_weeks:
            seen_weeks.add(ws)
            blocker_trends.append({
                "week": ws,
                "opened": weekly_opened.get(ws, 0),
                "resolved": weekly_resolved.get(ws, 0),
            })
        week_cursor += timedelta(weeks=1)

    # ── Weekly report summaries (weeks within selected range) ───────────────
    weekly_summaries = []
    week_start_d = since - timedelta(days=since.weekday())  # Monday of first week
    while week_start_d <= today:
        week_end_d = week_start_d + timedelta(days=6)
        # Skip weeks entirely before the range start
        if week_end_d < since:
            week_start_d += timedelta(weeks=1)
            continue
        ws = str(week_start_d)

        week_checkins = [
            c for c in checkins
            if max(week_start_d, since) <= c.date <= min(week_end_d, today)
        ]
        week_submitted = len(week_checkins)

        work_days = sum(
            1 for d in range(5)
            if since <= (week_start_d + timedelta(days=d)) <= today
        )
        expected = total_members * work_days
        rate = round(week_submitted / expected * 100, 1) if expected else 0

        week_member_counts: dict[str, int] = defaultdict(int)
        for c in week_checkins:
            week_member_counts[str(c.user_id)] += 1

        top_uid = max(week_member_counts, key=week_member_counts.get) if week_member_counts else None
        top_name = None
        if top_uid:
            for _, user in members:
                if str(user.id) == top_uid:
                    top_name = user.name or user.email
                    break

        weekly_summaries.append({
            "week_start": ws,
            "week_end": str(week_end_d),
            "total_checkins": week_submitted,
            "checkin_rate": rate,
            "top_member": top_name,
        })
        week_start_d += timedelta(weeks=1)

    # ── Overview stats ───────────────────────────────────────────────────────
    total_checkins = len(checkins)
    avg_rate = (
        round(total_checkins / (total_members * period_days) * 100, 1)
        if total_members else 0
    )

    all_blockers_result = await db.execute(
        select(Blocker).where(Blocker.team_id == team.id)
    )
    all_blockers = all_blockers_result.scalars().all()
    open_blockers = sum(1 for b in all_blockers if b.status != "resolved")
    resolved_blockers = sum(1 for b in all_blockers if b.status == "resolved")

    avg_streak = (
        round(sum(m["streak"] for m in member_stats) / len(member_stats), 1)
        if member_stats else 0
    )

    return {
        "team": {"id": str(team.id), "name": team.name, "plan": team_manager.plan},
        "period_days": period_days,
        "start_date": str(since),
        "end_date": str(today),
        "overview": {
            "total_members": total_members,
            "total_checkins": total_checkins,
            "avg_checkin_rate": avg_rate,
            "open_blockers": open_blockers,
            "resolved_blockers": resolved_blockers,
            "avg_streak": avg_streak,
        },
        "daily_rates": daily_rates,
        "member_stats": member_stats,
        "blocker_trends": blocker_trends,
        "submission_times": [
            {"hour": h, "label": f"{h:02d}:00", "count": time_dist[h]}
            for h in range(24)
            if time_dist.get(h, 0) > 0
        ],
        "weekly_summaries": weekly_summaries,
    }


# ─── Approximate exchange rates (USD base, updated manually as needed) ────────
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


# ─── Manager: team cost intelligence ─────────────────────────────────────────

@router.get("/{team_id}/cost-intelligence")
async def cost_intelligence(
    team_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Return per-member missed-day cost breakdown for the selected date range.
    Requires Starter plan. Manager-only.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    since, today, period_days = _parse_date_range(start_date, end_date)

    # ── Active members ──────────────────────────────────────────────────────
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()
    total_members = len(members)

    if total_members == 0:
        return {
            "team": {"id": str(team.id), "name": team.name, "plan": current_user.plan},
            "period_days": period_days,
            "summary": {
                "total_members": 0,
                "members_with_rate": 0,
                "members_missing_rate": 0,
                "members_hours_confirmed": 0,
                "total_missed_cost_usd": 0.0,
                "visibility_gap_pct": 0.0,
            },
            "members": [],
        }

    # ── Checkins in selected window grouped by user ─────────────────────────
    checkins_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= since,
                Checkin.date <= today,
            )
        )
    )
    checkins = checkins_result.scalars().all()

    checkin_count_by_user: dict[str, int] = defaultdict(int)
    for c in checkins:
        checkin_count_by_user[str(c.user_id)] += 1

    # ── Per-member cost computation ─────────────────────────────────────────
    member_rows = []
    total_missed_cost_usd = 0.0
    members_with_rate = 0
    members_hours_confirmed = 0
    total_visibility_gap_days = 0
    total_possible_days = 0

    for tm, user in members:
        uid = str(user.id)
        submitted = checkin_count_by_user.get(uid, 0)
        missed = max(0, period_days - submitted)

        has_rate = tm.hourly_rate is not None
        if has_rate:
            members_with_rate += 1
        if tm.hours_confirmed:
            members_hours_confirmed += 1

        hours = tm.hours_per_day if (tm.hours_per_day is not None and tm.hours_per_day > 0) else 8.0
        rate = _RATES_TO_USD.get((tm.currency or "INR").upper(), 0.012)
        hourly_usd = (tm.hourly_rate or 0.0) * rate
        missed_cost_usd = round(hourly_usd * hours * missed, 2)

        if has_rate:
            total_missed_cost_usd += missed_cost_usd

        total_visibility_gap_days += missed
        total_possible_days += period_days

        # Hours source: 'confirmed' | 'estimated' | 'not_set'
        if tm.hourly_rate is None:
            hours_status = "not_set"
        elif tm.hours_confirmed:
            hours_status = "confirmed"
        else:
            hours_status = "estimated"

        member_rows.append({
            "user_id": uid,
            "name": user.name or user.email,
            "hourly_rate": tm.hourly_rate,
            "currency": tm.currency or "INR",
            "hours_per_day": hours,
            "hours_confirmed": tm.hours_confirmed or False,
            "hours_status": hours_status,
            "submitted_checkins": submitted,
            "missed_checkins": missed,
            "checkin_rate": round(submitted / period_days * 100, 1),
            "missed_cost_usd": missed_cost_usd if has_rate else None,
            "hourly_rate_usd": round(hourly_usd, 2) if has_rate else None,
        })

    # Sort by missed_cost_usd descending (members without rates go to the bottom)
    member_rows.sort(
        key=lambda m: (m["missed_cost_usd"] is None, -(m["missed_cost_usd"] or 0))
    )

    members_missing_rate = total_members - members_with_rate
    visibility_gap_pct = (
        round(total_visibility_gap_days / total_possible_days * 100, 1)
        if total_possible_days > 0 else 0.0
    )

    return {
        "team": {"id": str(team.id), "name": team.name, "plan": current_user.plan},
        "period_days": period_days,
        "summary": {
            "total_members": total_members,
            "members_with_rate": members_with_rate,
            "members_missing_rate": members_missing_rate,
            "members_hours_confirmed": members_hours_confirmed,
            "total_missed_cost_usd": round(total_missed_cost_usd, 2),
            "visibility_gap_pct": visibility_gap_pct,
        },
        "members": member_rows,
    }


# ─── Manager: blocked cost ────────────────────────────────────────────────────

@router.get("/{team_id}/blocked-cost")
async def blocked_cost(
    team_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Quantify the cost of open/in-progress blockers.
    A blocker is an explicit declaration of being stuck — unlike task mentions,
    this is unambiguous. Cost = hours_open × hourly_rate_usd (hourly, not daily).
    Also returns avg resolution time for resolved blockers in the selected date range.
    Starter plan only. Manager-only.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    since, today, period_days = _parse_date_range(start_date, end_date)
    now = datetime.utcnow()
    since_dt = datetime.combine(since, datetime.min.time())

    # ── Active members for rate lookup ──────────────────────────────────────
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()
    member_map: dict[str, tuple] = {str(user.id): (tm, user) for tm, user in members}

    # ── Active blockers (not yet resolved) ─────────────────────────────────
    active_result = await db.execute(
        select(Blocker)
        .where(
            and_(
                Blocker.team_id == team.id,
                Blocker.status.in_(["open", "acknowledged", "in_progress"]),
            )
        )
        .order_by(Blocker.created_at)
    )
    active_blockers = active_result.scalars().all()

    # ── Resolved blockers in selected date range (for avg resolution time) ──
    resolved_result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.status == "resolved",
                Blocker.resolved_at.isnot(None),
                Blocker.created_at >= since_dt,
            )
        )
    )
    resolved_blockers = resolved_result.scalars().all()

    # ── Duration formatting helper ──────────────────────────────────────────
    def _fmt_duration(hours: float) -> str:
        if hours < 1:
            return "< 1h"
        total_min = int(hours * 60)
        days = total_min // (24 * 60)
        rem_h = (total_min % (24 * 60)) // 60
        if days == 0:
            return f"{rem_h}h"
        if rem_h == 0:
            return f"{days}d"
        return f"{days}d {rem_h}h"

    # ── Per-blocker cost ─────────────────────────────────────────────────────
    blocker_rows: list[dict] = []
    total_blocked_cost_usd = 0.0

    for b in active_blockers:
        uid = str(b.user_id)
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        duration_label = _fmt_duration(hours_open)
        tm, user = member_map.get(uid, (None, None))

        has_rate = tm is not None and tm.hourly_rate is not None
        if has_rate:
            rate_usd = _RATES_TO_USD.get((tm.currency or "INR").upper(), 0.012)
            hourly_usd = tm.hourly_rate * rate_usd
            blocked_cost_usd = round(hourly_usd * hours_open, 2)
            total_blocked_cost_usd += blocked_cost_usd
        else:
            blocked_cost_usd = None

        blocker_rows.append({
            "blocker_id": str(b.id),
            "title": b.title,
            "status": b.status,
            "reporter_id": uid,
            "reporter_name": (user.name or user.email) if user else "Unknown",
            "hours_open": round(hours_open, 1),
            "duration_label": duration_label,
            "blocked_cost_usd": blocked_cost_usd,
            "hourly_rate_usd": round(hourly_usd, 6) if has_rate else None,
            "created_at_iso": b.created_at.isoformat() + "Z",
            "has_rate": has_rate,
            "created_at": b.created_at.date().isoformat(),
        })

    # Sort by blocked_cost_usd descending (no-rate rows at the bottom)
    blocker_rows.sort(
        key=lambda r: (r["blocked_cost_usd"] is None, -(r["blocked_cost_usd"] or 0))
    )

    # ── Avg resolution time in hours (last 30 days) ──────────────────────────
    resolution_hours = [
        (b.resolved_at - b.created_at).total_seconds() / 3600
        for b in resolved_blockers
        if b.resolved_at and b.created_at
    ]
    avg_resolution_hours = round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None
    avg_resolution_label = _fmt_duration(avg_resolution_hours) if avg_resolution_hours is not None else None

    return {
        "period_days": period_days,
        "active_blockers": blocker_rows,
        "summary": {
            "active_count": len(blocker_rows),
            "total_blocked_cost_usd": round(total_blocked_cost_usd, 2),
            "resolved_last_30d": len(resolved_blockers),
            "avg_resolution_hours": avg_resolution_hours,
            "avg_resolution_label": avg_resolution_label,
        },
    }


# ─── Manager: monthly blocked cost ───────────────────────────────────────────

@router.get("/{team_id}/monthly-cost")
async def monthly_cost(
    team_id: str,
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    current_user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Cost of blockers active (overlapping) in the selected calendar month.
    Only the hours within the month boundary are counted per blocker.
    Manager-only, Starter plan required.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    now = datetime.utcnow()

    # Month boundaries (UTC midnight)
    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    # Active members for rate lookup
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    member_map: dict[str, tuple] = {
        str(user.id): (tm, user) for tm, user in members_result.all()
    }

    # Blockers active during month: created before month_end AND (unresolved OR resolved after month_start)
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.created_at < month_end,
                or_(
                    Blocker.resolved_at.is_(None),
                    Blocker.resolved_at > month_start,
                ),
            )
        )
    )
    blockers = result.scalars().all()

    # Per-member aggregation
    member_costs: dict[str, dict] = {}
    total_cost_usd = 0.0
    total_blocker_count = 0

    for b in blockers:
        uid = str(b.user_id)
        tm, user = member_map.get(uid, (None, None))
        if tm is None or tm.hourly_rate is None:
            continue

        # Overlap window within the selected month
        effective_start = max(b.created_at, month_start)
        effective_end = min(b.resolved_at if b.resolved_at else now, month_end)
        overlap_hours = max(0.0, (effective_end - effective_start).total_seconds() / 3600)
        if overlap_hours <= 0:
            continue

        rate_usd = _RATES_TO_USD.get((tm.currency or "INR").upper(), 0.012)
        hourly_usd = tm.hourly_rate * rate_usd
        cost = hourly_usd * overlap_hours

        total_cost_usd += cost
        total_blocker_count += 1

        if uid not in member_costs:
            member_costs[uid] = {
                "member_name": (user.name or user.email) if user else "Unknown",
                "blocker_count": 0,
                "cost_usd": 0.0,
                "blockers": [],
            }
        member_costs[uid]["blocker_count"] += 1
        member_costs[uid]["cost_usd"] += cost
        member_costs[uid]["blockers"].append({
            "id": str(b.id),
            "title": b.title,
            "status": b.status,
        })

    members_list = sorted(
        member_costs.values(), key=lambda r: r["cost_usd"], reverse=True
    )
    for m in members_list:
        m["cost_usd"] = round(m["cost_usd"], 2)

    return {
        "year": year,
        "month": month,
        "total_cost_usd": round(total_cost_usd, 2),
        "blocker_count": total_blocker_count,
        "members": members_list,
    }


# ─── Manager: participation trend ─────────────────────────────────────────────

@router.get("/{team_id}/participation-trend")
async def participation_trend(
    team_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Return per-member check-in rate across four equal windows of the selected range.
    Detects declining engagement before it becomes a missed-cost problem.
    Starter plan only. Manager-only.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(current_user)

    since, today, period_days = _parse_date_range(start_date, end_date)

    # ── Active members ──────────────────────────────────────────────────────
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()

    if not members:
        return {"trend_days": period_days, "members": [], "team_trend": "stable"}

    # ── Checkins in selected window ─────────────────────────────────────────
    checkins_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.team_id == team.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= since,
                Checkin.date <= today,
            )
        )
    )
    checkins = checkins_result.scalars().all()

    # Group checkin dates by user
    checkin_dates_by_user: dict[str, list[date]] = defaultdict(list)
    for c in checkins:
        checkin_dates_by_user[str(c.user_id)].append(c.date)

    # ── Build 4 equal windows across the selected period ───────────────────
    window_size = max(1, period_days // 4)
    window_starts = [since + timedelta(days=i * window_size) for i in range(4)]

    def _window_rate(uid: str, win_start: date, win_days: int) -> float:
        win_end = win_start + timedelta(days=win_days - 1)
        submitted = sum(
            1 for d in checkin_dates_by_user.get(uid, [])
            if win_start <= d <= win_end
        )
        return round(submitted / win_days * 100, 1) if win_days > 0 else 0.0

    def _trend_label(rates: list[float]) -> str:
        if len(rates) < 2:
            return "stable"
        first_half = sum(rates[:2]) / 2
        second_half = sum(rates[2:]) / 2
        delta = second_half - first_half
        if delta < -15:
            return "declining"
        if delta > 15:
            return "improving"
        return "stable"

    # ── Per-member trend ────────────────────────────────────────────────────
    member_rows: list[dict] = []
    for tm, user in members:
        uid = str(user.id)
        weekly_rates = [_window_rate(uid, ws, window_size) for ws in window_starts]
        trend = _trend_label(weekly_rates)
        current_rate = weekly_rates[-1]
        at_risk = trend == "declining" and current_rate < 50.0

        member_rows.append({
            "user_id": uid,
            "name": user.name or user.email,
            "weekly_rates": weekly_rates,
            "week_labels": [ws.strftime("%b %d") for ws in window_starts],
            "trend": trend,
            "at_risk": at_risk,
            "current_week_rate": current_rate,
        })

    # Sort: at-risk first, then declining, then stable, then improving
    _sort_order = {"declining": 0, "stable": 1, "improving": 2}
    member_rows.sort(key=lambda m: (not m["at_risk"], _sort_order.get(m["trend"], 1), -m["current_week_rate"]))

    # ── Team-level trend ────────────────────────────────────────────────────
    declining_count = sum(1 for m in member_rows if m["trend"] == "declining")
    improving_count = sum(1 for m in member_rows if m["trend"] == "improving")
    if declining_count > len(member_rows) / 2:
        team_trend = "declining"
    elif improving_count > len(member_rows) / 2:
        team_trend = "improving"
    else:
        team_trend = "stable"

    return {
        "trend_days": period_days,
        "members": member_rows,
        "team_trend": team_trend,
        "at_risk_count": sum(1 for m in member_rows if m["at_risk"]),
    }
