"""Blocker Intelligence — manager-only analytics dashboard endpoints.

Backs the redesigned /ai-blockers page. All endpoints take `team_id` as a
query parameter and are scoped to teams the caller manages.
"""
import csv
import io
import logging
import uuid as _uuid
from collections import Counter
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Blocker, Team, TeamMember, User
from auth import require_manager
from plan_limits import require_starter as _require_starter_base
from routers.reports import _RATES_TO_USD


def _require_starter(manager: User) -> None:
    _require_starter_base(manager, "Blocker Intelligence")

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _parse_dates(start_date: Optional[str], end_date: Optional[str]) -> tuple[date, date, int]:
    today = date.today()
    end_d = date.fromisoformat(end_date) if end_date else today
    end_d = min(end_d, today)
    start_d = date.fromisoformat(start_date) if start_date else end_d - timedelta(days=29)
    if start_d > end_d:
        start_d = end_d
    return start_d, end_d, (end_d - start_d).days + 1


def _fmt_duration(hours: float) -> str:
    if hours is None:
        return "—"
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


def _fmt_age_days(hours: float) -> str:
    """Compact label like '8d' or '36h' for KPI cards."""
    if hours is None:
        return "—"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


CATEGORY_KEYWORDS = (
    ("Infrastructure",        ("ci/cd", "ci ", "pipeline", "deploy", "deployment", "ssh", "docker", "kubernetes", "k8s", "aws", "production", "infra", "server", "database", "merge to main", "release")),
    ("Process & Automation",  ("manual", "process", "documentation", "onboarding", "report", "compiled", "automation", "sprint", "weekly", "ramp-up", "training")),
    ("Third-party",           ("api", "third-party", "third party", "integration", "external", "rate limit", "vendor")),
    ("Testing",               ("test ", " test", "testing", "qa", "unit test", "e2e", "regression", "test data")),
)


def infer_category(title: str) -> str:
    t = (title or "").lower()
    for label, keywords in CATEGORY_KEYWORDS:
        if any(k in t for k in keywords):
            return label
    return "Other"


def _hourly_rate_usd(tm: TeamMember | None) -> float | None:
    if tm is None or tm.hourly_rate is None:
        return None
    rate = _RATES_TO_USD.get((tm.currency or "INR").upper(), 0.012)
    return tm.hourly_rate * rate


def _impact_label(category: str, hours_open: float, status: str) -> str:
    if status == "resolved":
        return ""
    if category == "Infrastructure":
        return "Blocking releases & merges"
    if category == "Process & Automation":
        return "Recoverable via automation"
    if category == "Third-party":
        return "External dependency blocking work"
    if category == "Testing":
        return "Slowing release cadence"
    return "Open for " + _fmt_duration(hours_open)


async def _resolve_team(team_id: str, current_user: User, db: AsyncSession) -> Team:
    """Verify team_id is a UUID owned by current_user (manager). Returns Team."""
    try:
        team_uuid = _uuid.UUID(team_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team_id")
    result = await db.execute(select(Team).where(Team.id == team_uuid))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if team.manager_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the manager of this team")
    return team


async def _load_member_rate_map(team_id, db: AsyncSession) -> dict[str, tuple[TeamMember, User]]:
    result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team_id, TeamMember.status == "active"))
    )
    return {str(user.id): (tm, user) for tm, user in result.all()}


async def _load_active_blockers(team_id, db: AsyncSession) -> list[Blocker]:
    result = await db.execute(
        select(Blocker)
        .where(
            and_(
                Blocker.team_id == team_id,
                Blocker.status.in_(["open", "acknowledged", "in_progress"]),
            )
        )
        .order_by(Blocker.created_at)
    )
    return list(result.scalars().all())


def _assignee_id(b: Blocker) -> str:
    return str(b.assigned_to) if b.assigned_to else str(b.user_id)


# ─── BI-1: KPI summary ───────────────────────────────────────────────────────

@router.get("/summary")
async def summary(
    team_id: str = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    _require_starter(current_user)
    team = await _resolve_team(team_id, current_user, db)
    start_d, end_d, period_days = _parse_dates(start_date, end_date)
    now = datetime.utcnow()

    member_map = await _load_member_rate_map(team.id, db)
    active = await _load_active_blockers(team.id, db)

    # Revenue at risk + per-hour burn rate (current open blockers)
    revenue_at_risk = 0.0
    per_hour_usd = 0.0
    for b in active:
        rate = _hourly_rate_usd(member_map.get(_assignee_id(b), (None, None))[0])
        if rate is None:
            continue
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        revenue_at_risk += rate * hours_open
        per_hour_usd += rate

    # Resolved within current range
    cur_start_dt = datetime.combine(start_d, datetime.min.time())
    cur_end_dt = datetime.combine(end_d, datetime.max.time())
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.status == "resolved",
                Blocker.resolved_at.isnot(None),
                Blocker.resolved_at >= cur_start_dt,
                Blocker.resolved_at <= cur_end_dt,
            )
        )
    )
    resolved_current = list(result.scalars().all())

    # Resolved within previous (same-length) range for delta comparison
    prev_end_dt = cur_start_dt - timedelta(seconds=1)
    prev_start_dt = prev_end_dt - timedelta(days=period_days - 1)
    prev_start_dt = prev_start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.status == "resolved",
                Blocker.resolved_at.isnot(None),
                Blocker.resolved_at >= prev_start_dt,
                Blocker.resolved_at <= prev_end_dt,
            )
        )
    )
    resolved_previous = list(result.scalars().all())

    def _avg_hours(items: list[Blocker]) -> float | None:
        diffs = [(b.resolved_at - b.created_at).total_seconds() / 3600 for b in items if b.resolved_at and b.created_at]
        return (sum(diffs) / len(diffs)) if diffs else None

    avg_hours_current = _avg_hours(resolved_current)
    avg_hours_previous = _avg_hours(resolved_previous)

    # Avg resolution delta — improvement = faster (lower hours)
    if avg_hours_current is None:
        avg_resolution_label = "—"
        avg_delta_label = ""
        avg_delta_tone = "neutral"
    else:
        avg_resolution_label = _fmt_age_days(avg_hours_current)
        if avg_hours_previous is None or avg_hours_previous == 0:
            avg_delta_label = "no prior data"
            avg_delta_tone = "neutral"
        else:
            delta_hours = avg_hours_current - avg_hours_previous
            delta_days = round(delta_hours / 24, 1)
            if abs(delta_hours) < 1:
                avg_delta_label = "no change vs prior"
                avg_delta_tone = "neutral"
            else:
                sign = "+" if delta_hours > 0 else "-"
                avg_delta_label = f"{sign}{abs(delta_days)}d vs prior {period_days}d"
                # Lower is better → faster
                avg_delta_tone = "up" if delta_hours < 0 else "down"

    resolved_delta = len(resolved_current) - len(resolved_previous)
    if resolved_delta == 0:
        resolved_delta_label = "same as prior"
        resolved_delta_tone = "neutral"
    else:
        sign = "+" if resolved_delta > 0 else ""
        resolved_delta_label = f"{sign}{resolved_delta} vs prior {period_days}d"
        resolved_delta_tone = "up" if resolved_delta > 0 else "down"

    open_count = len(active)
    if open_count == 0:
        open_label = "all clear"
    elif open_count <= 2:
        open_label = "monitor"
    else:
        open_label = "needs attention"

    return {
        "revenue_at_risk": {
            "amount_usd": round(revenue_at_risk, 2),
            "open_blockers_count": open_count,
        },
        "open_blockers": {
            "count": open_count,
            "label": open_label,
        },
        "avg_resolution": {
            "label": avg_resolution_label,
            "delta_label": avg_delta_label,
            "delta_tone": avg_delta_tone,
        },
        "burn_rate": {
            "per_hour_usd": round(per_hour_usd, 2),
            "per_day_usd": round(per_hour_usd * 24, 2),
            "per_week_usd": round(per_hour_usd * 24 * 7, 2),
        },
        "resolved_this_period": {
            "count": len(resolved_current),
            "delta_label": resolved_delta_label,
            "delta_tone": resolved_delta_tone,
        },
        "range": {
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "period_days": period_days,
        },
    }


# ─── BI-3: Alert banner ──────────────────────────────────────────────────────

@router.get("/alert")
async def alert(
    team_id: str = Query(...),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    _require_starter(current_user)
    team = await _resolve_team(team_id, current_user, db)
    now = datetime.utcnow()

    member_map = await _load_member_rate_map(team.id, db)
    active = await _load_active_blockers(team.id, db)

    # Per-blocker hourly impact
    scored = []
    total_hourly = 0.0
    for b in active:
        tm = member_map.get(_assignee_id(b), (None, None))[0]
        rate = _hourly_rate_usd(tm)
        if rate is None:
            continue
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        cumulative = rate * hours_open
        total_hourly += rate
        scored.append({"blocker": b, "rate": rate, "cumulative": cumulative})

    # Top-2 by cumulative loss
    scored.sort(key=lambda r: r["cumulative"], reverse=True)
    top_n = scored[:2]
    top_n_count = len(top_n)
    top_n_savings_weekly = round(sum(r["rate"] for r in top_n) * 24 * 7, 2)

    # Engineers unblocked = distinct assignees on top-N
    unblocks = {_assignee_id(r["blocker"]) for r in top_n}

    return {
        "hourly_loss_usd": round(total_hourly, 2),
        "top_n_count": top_n_count,
        "top_n_savings_weekly_usd": top_n_savings_weekly,
        "unblocks_engineers": len(unblocks),
        "suggest_fixes_url": f"/ai-radar?team_id={team_id}",
    }


# ─── BI-5: Live cost-series ──────────────────────────────────────────────────

@router.get("/cost-series")
async def cost_series(
    team_id: str = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    cumulative: bool = Query(True),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    _require_starter(current_user)
    team = await _resolve_team(team_id, current_user, db)
    start_d, end_d, period_days = _parse_dates(start_date, end_date)
    now = datetime.utcnow()

    member_map = await _load_member_rate_map(team.id, db)

    # All blockers that overlapped any day in the range (open during range OR resolved within range)
    range_end_dt = datetime.combine(end_d, datetime.max.time())
    range_start_dt = datetime.combine(start_d, datetime.min.time())
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.created_at <= range_end_dt,
                or_(
                    Blocker.resolved_at.is_(None),
                    Blocker.resolved_at >= range_start_dt,
                ),
            )
        )
    )
    blockers = list(result.scalars().all())

    # Daily burn (non-cumulative) — for each day, sum of overlap-hours × hourly_rate
    daily_burn: dict[date, float] = {}
    cursor = start_d
    while cursor <= end_d:
        daily_burn[cursor] = 0.0
        cursor += timedelta(days=1)

    for b in blockers:
        rate = _hourly_rate_usd(member_map.get(_assignee_id(b), (None, None))[0])
        if rate is None or rate <= 0:
            continue
        b_end_dt = b.resolved_at if b.resolved_at else now
        cur = start_d
        while cur <= end_d:
            day_start = datetime.combine(cur, datetime.min.time())
            day_end = datetime.combine(cur, datetime.max.time())
            ov_start = max(b.created_at, day_start)
            ov_end = min(b_end_dt, day_end)
            if ov_end > ov_start:
                hours = (ov_end - ov_start).total_seconds() / 3600
                daily_burn[cur] += rate * hours
            cur += timedelta(days=1)

    # Build series
    points = []
    running = 0.0
    cursor = start_d
    while cursor <= end_d:
        running += daily_burn[cursor]
        value = running if cumulative else daily_burn[cursor]
        points.append({"date": cursor.isoformat(), "value": round(value, 2)})
        cursor += timedelta(days=1)

    # Live total = sum of (hours_open × rate) across currently-active blockers
    live_total = 0.0
    per_hour_usd = 0.0
    for b in blockers:
        if b.status == "resolved":
            continue
        rate = _hourly_rate_usd(member_map.get(_assignee_id(b), (None, None))[0])
        if rate is None:
            continue
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        live_total += rate * hours_open
        per_hour_usd += rate

    return {
        "currency": "USD",
        "live_total_usd": round(live_total, 2),
        "burn_rate": {
            "per_hour_usd": round(per_hour_usd, 2),
            "per_day_usd": round(per_hour_usd * 24, 2),
            "per_week_usd": round(per_hour_usd * 24 * 7, 2),
        },
        "points": points,
    }


# ─── BI-6: Why this matters ─────────────────────────────────────────────────

@router.get("/insights")
async def insights(
    team_id: str = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(4, ge=1, le=10),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    _require_starter(current_user)
    team = await _resolve_team(team_id, current_user, db)
    start_d, end_d, period_days = _parse_dates(start_date, end_date)
    now = datetime.utcnow()

    member_map = await _load_member_rate_map(team.id, db)
    active = await _load_active_blockers(team.id, db)

    # Per-blocker open hours + cumulative cost
    enriched = []
    total_cost = 0.0
    for b in active:
        tm = member_map.get(_assignee_id(b), (None, None))[0]
        rate = _hourly_rate_usd(tm) or 0.0
        hours_open = max(0.0, (now - b.created_at).total_seconds() / 3600)
        cost = rate * hours_open
        category = infer_category(b.title)
        enriched.append({
            "blocker": b,
            "hours_open": hours_open,
            "cost": cost,
            "category": category,
        })
        total_cost += cost

    out: list[dict] = []

    # 1. Longest running
    if enriched:
        longest = max(enriched, key=lambda r: r["hours_open"])
        team_size = len(member_map) or 1
        out.append({
            "kind": "longest",
            "title": "Longest running blocker",
            "body": f"{longest['blocker'].title[:80]} has been open for {_fmt_duration(longest['hours_open'])}.",
            "blocker_id": str(longest["blocker"].id),
        })

    # 2. Highest revenue impact
    if enriched and total_cost > 0:
        highest = max(enriched, key=lambda r: r["cost"])
        pct = (highest["cost"] / total_cost) * 100 if total_cost > 0 else 0
        out.append({
            "kind": "highest",
            "title": "Highest revenue impact",
            "body": f"{highest['blocker'].title[:80]} contributes {pct:.1f}% of total blocked cost.",
            "blocker_id": str(highest["blocker"].id),
        })

    # 3. Top recurring category
    if enriched:
        cat_counts = Counter(r["category"] for r in enriched)
        top_cat, top_n = cat_counts.most_common(1)[0]
        pct = (top_n / len(enriched)) * 100
        if pct >= 25:
            out.append({
                "kind": "recurring",
                "title": "Top recurring category",
                "body": f"{pct:.0f}% of open blockers fall under {top_cat} — strong AI Radar candidates.",
                "category": top_cat,
            })

    # 4. Resolved this period
    cur_start_dt = datetime.combine(start_d, datetime.min.time())
    cur_end_dt = datetime.combine(end_d, datetime.max.time())
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.status == "resolved",
                Blocker.resolved_at.isnot(None),
                Blocker.resolved_at >= cur_start_dt,
                Blocker.resolved_at <= cur_end_dt,
            )
        )
    )
    resolved = list(result.scalars().all())
    if resolved:
        # Estimated recovered = sum of (resolved_at - created_at) × rate for resolved blockers
        recovered = 0.0
        for b in resolved:
            tm = member_map.get(_assignee_id(b), (None, None))[0]
            rate = _hourly_rate_usd(tm) or 0.0
            hours = max(0.0, (b.resolved_at - b.created_at).total_seconds() / 3600)
            recovered += rate * hours
        out.append({
            "kind": "resolved",
            "title": "Resolved this period",
            "body": f"{len(resolved)} blocker{'s' if len(resolved) != 1 else ''} closed, recovering an estimated ${recovered:,.0f}.",
        })

    return {"insights": out[:limit]}


# ─── BI-7: Export ───────────────────────────────────────────────────────────

@router.get("/export")
async def export_blockers(
    team_id: str = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    format: str = Query("csv"),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    if format != "csv":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only format=csv is supported")

    _require_starter(current_user)
    team = await _resolve_team(team_id, current_user, db)
    start_d, end_d, _ = _parse_dates(start_date, end_date)
    now = datetime.utcnow()

    member_map = await _load_member_rate_map(team.id, db)

    range_start_dt = datetime.combine(start_d, datetime.min.time())
    range_end_dt = datetime.combine(end_d, datetime.max.time())

    # All blockers created on/before end_d and either still open or resolved within range
    result = await db.execute(
        select(Blocker).where(
            and_(
                Blocker.team_id == team.id,
                Blocker.created_at <= range_end_dt,
                or_(
                    Blocker.resolved_at.is_(None),
                    Blocker.resolved_at >= range_start_dt,
                ),
            )
        ).order_by(Blocker.created_at.desc())
    )
    blockers = list(result.scalars().all())

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "title", "status", "category", "owner", "owner_email",
        "created_at", "resolved_at", "open_label", "hours_open",
        "revenue_impact_usd", "revenue_impact_label",
    ])

    for b in blockers:
        uid = _assignee_id(b)
        tm, user = member_map.get(uid, (None, None))
        owner_name = user.name if user else ""
        owner_email = user.email if user else ""
        end_dt = b.resolved_at if b.resolved_at else now
        hours_open = max(0.0, (end_dt - b.created_at).total_seconds() / 3600)
        rate = _hourly_rate_usd(tm)
        revenue = round(rate * hours_open, 2) if rate is not None else ""
        category = infer_category(b.title)
        impact_label = _impact_label(category, hours_open, b.status)
        writer.writerow([
            str(b.id), b.title, b.status, category, owner_name, owner_email,
            b.created_at.isoformat() if b.created_at else "",
            b.resolved_at.isoformat() if b.resolved_at else "",
            _fmt_duration(hours_open) if b.status != "resolved" else "—",
            round(hours_open, 1),
            revenue,
            impact_label,
        ])

    buf.seek(0)
    filename = f"blocker-intelligence-{team_id}-{start_d}-{end_d}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
