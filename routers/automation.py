"""
Automation Radar router.

Endpoints:
  GET  /automation/{team_id}/history                — list past runs (max 20)
  POST /automation/{team_id}/run                    — trigger new analysis (once per week)
  GET  /automation/{team_id}/history/{analysis_id}  — full detail of a single run
"""
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from database import get_db
from models import (
    AutomationAnalysis,
    Blocker,
    Checkin,
    CheckinAnswer,
    Team,
    TeamMember,
    TeamQuestion,
    User,
)
from schemas import (
    AutomationAnalysisDetailResponse,
    AutomationAnalysisSummaryResponse,
    AutomationRunRequest,
)
from auth import require_manager, require_team_manager
from llm_service import generate_automation_insights
from plan_limits import require_starter as _require_starter_base

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_starter(team: Team) -> None:
    _require_starter_base(team, "Automation Radar")


# ---------------------------------------------------------------------------
# Data aggregation helpers
# ---------------------------------------------------------------------------

def _tokenize_phrases(text: str) -> list[str]:
    """
    Split a free-text answer into meaningful phrases (3-8 words).
    Strategy: split on sentence/clause boundaries, then keep chunks of 3-8 words.
    """
    # Split on sentence/clause delimiters
    parts = re.split(r"[.;,\n]|(?:\s+and\s+)|(?:\s+then\s+)", text.lower())
    phrases = []
    for part in parts:
        part = part.strip()
        words = part.split()
        if 3 <= len(words) <= 8:
            phrases.append(" ".join(words))
        elif len(words) > 8:
            # Slide a 4-word window to catch sub-phrases
            for i in range(len(words) - 3):
                phrases.append(" ".join(words[i : i + 4]))
    return phrases


def _aggregate_checkin_data(
    member_answers: dict[str, list[str]],   # user_name → list of raw answer strings
    member_counts: dict[str, int],          # user_name → submitted checkin count
) -> str:
    """Build the compact per-member task summary for the LLM prompt."""
    if not member_answers:
        return "(no check-in answer data)"

    lines = []
    for name, answers in member_answers.items():
        submitted = member_counts.get(name, 0)
        all_phrases: list[str] = []
        for answer in answers:
            if answer and answer.strip():
                all_phrases.extend(_tokenize_phrases(answer))

        if not all_phrases:
            lines.append(f"Member '{name}': submitted {submitted} check-ins. No detailed task text found.")
            continue

        counter = Counter(all_phrases)
        # Token budget: top-8 per member (reduced to 5 if many members)
        top_n = 5 if len(member_answers) > 8 else 8
        top = counter.most_common(top_n)
        top_str = ", ".join(f"'{phrase}' (×{count})" for phrase, count in top if count > 1)
        if top_str:
            lines.append(f"Member '{name}': submitted {submitted} check-ins. Repeated tasks: {top_str}")
        else:
            lines.append(f"Member '{name}': submitted {submitted} check-ins. No strongly repeated patterns found.")

    return "\n".join(lines)


def _aggregate_blocker_data(blocker_rows: list) -> str:
    """Build the compact recurring blocker summary for the LLM prompt."""
    if not blocker_rows:
        return "(no blocker data)"

    # Group by normalised title (lowercase, stripped)
    title_map: dict[str, list[str]] = defaultdict(list)   # normalised_title → [member_name, ...]
    for title, member_name in blocker_rows:
        key = title.lower().strip()
        title_map[key].append(member_name)

    # Count and sort
    sorted_blockers = sorted(title_map.items(), key=lambda x: -len(x[1]))

    # Top 10 to stay within token budget
    lines = []
    for title, members in sorted_blockers[:10]:
        unique_members = list(dict.fromkeys(members))  # deduplicate, preserve order
        member_str = ", ".join(unique_members[:5])
        count = len(members)
        lines.append(f"'{title}' (×{count}, member{'s' if count > 1 else ''}: {member_str})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GET /automation/{team_id}/history
# ---------------------------------------------------------------------------

@router.get("/{team_id}/history", response_model=list[AutomationAnalysisSummaryResponse])
async def list_analysis_history(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Return the last 20 automation analysis runs for a team. Starter plan only."""
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(team)

    result = await db.execute(
        select(AutomationAnalysis)
        .where(AutomationAnalysis.team_id == team.id)
        .order_by(AutomationAnalysis.created_at.desc())
        .limit(20)
    )
    rows = result.scalars().all()

    return [
        AutomationAnalysisSummaryResponse(
            id=str(r.id),
            team_id=str(r.team_id),
            window_days=r.window_days,
            status=r.status,
            period_start=r.period_start,
            period_end=r.period_end,
            summary_text=r.summary_text,
            error_message=r.error_message,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# POST /automation/{team_id}/run
# ---------------------------------------------------------------------------

@router.post("/{team_id}/run", response_model=AutomationAnalysisDetailResponse, status_code=status.HTTP_201_CREATED)
async def run_analysis(
    team_id: str,
    data: AutomationRunRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger a new Automation Radar analysis. One completed run allowed per team per week
    (Monday–Sunday). Starter plan only. Returns stored findings immediately.
    """
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(team)

    # ── Weekly throttle ──────────────────────────────────────────────────────
    today = date.today()
    week_monday = today - timedelta(days=today.weekday())  # ISO Monday of current week
    week_monday_dt = datetime.combine(week_monday, datetime.min.time())

    existing_result = await db.execute(
        select(AutomationAnalysis).where(
            and_(
                AutomationAnalysis.team_id == team.id,
                AutomationAnalysis.status == "completed",
                AutomationAnalysis.created_at >= week_monday_dt,
            )
        )
    )
    existing_run = existing_result.scalar_one_or_none()
    if existing_run:
        next_monday = week_monday + timedelta(days=7)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Analysis already run this week. View it in your history.",
                "existing_analysis_id": str(existing_run.id),
                "next_available": str(next_monday),
            },
        )

    # ── Compute window ────────────────────────────────────────────────────────
    period_end = today
    period_start = today - timedelta(days=data.window_days - 1)
    period_start_dt = datetime.combine(period_start, datetime.min.time())

    # ── Load active members of the team ──────────────────────────────────────
    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    members = members_result.all()

    # ── Aggregate check-in answers ────────────────────────────────────────────
    # One query: join CheckinAnswer → TeamQuestion → Checkin per team/window
    # Only non-blocker questions (they contain task descriptions)
    answers_result = await db.execute(
        select(CheckinAnswer, TeamQuestion, User, Checkin)
        .join(Checkin, CheckinAnswer.checkin_id == Checkin.id)
        .join(TeamQuestion, CheckinAnswer.question_id == TeamQuestion.id)
        .join(User, Checkin.user_id == User.id)
        .where(
            and_(
                Checkin.team_id == team.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= period_start,
                TeamQuestion.is_blocker_type == False,  # noqa: E712
            )
        )
    )
    answer_rows = answers_result.all()

    # Group answers and submitted checkin counts by member name
    member_answers: dict[str, list[str]] = defaultdict(list)
    member_checkin_ids: dict[str, set] = defaultdict(set)
    for ca, tq, user, checkin in answer_rows:
        name = user.name or user.email
        if ca.answer and ca.answer.strip():
            member_answers[name].append(ca.answer)
        member_checkin_ids[name].add(str(checkin.id))

    member_counts = {name: len(ids) for name, ids in member_checkin_ids.items()}

    # Token budget guard: if data is very large, trim to top-8 most active members
    if len(member_answers) > 10:
        top_members = sorted(member_counts, key=lambda n: -member_counts[n])[:10]
        member_answers = {n: member_answers[n] for n in top_members}
        member_counts = {n: member_counts[n] for n in top_members}

    aggregated_task_data = _aggregate_checkin_data(member_answers, member_counts)

    # Safety truncate to ~6000 chars
    if len(aggregated_task_data) > 6000:
        aggregated_task_data = aggregated_task_data[:6000] + "\n[... truncated for token budget ...]"

    # ── Aggregate blockers ────────────────────────────────────────────────────
    blockers_result = await db.execute(
        select(Blocker, User)
        .join(User, Blocker.user_id == User.id)
        .where(
            and_(
                Blocker.team_id == team.id,
                Blocker.created_at >= period_start_dt,
            )
        )
    )
    blocker_rows_raw = blockers_result.all()
    blocker_rows = [(b.title, user.name or user.email) for b, user in blocker_rows_raw]
    blocker_data = _aggregate_blocker_data(blocker_rows)

    # ── Guard: no data at all ──────────────────────────────────────────────────
    has_task_data = any(v for v in member_answers.values())
    has_blocker_data = bool(blocker_rows)
    if not has_task_data and not has_blocker_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No check-in or blocker data found in the last {data.window_days} days. "
                "Ask your team to submit check-ins first."
            ),
        )

    # ── Call LLM ──────────────────────────────────────────────────────────────
    try:
        llm_result = await generate_automation_insights(
            aggregated_task_data, blocker_data, data.window_days
        )
    except Exception as exc:
        logger.error("LLM call failed for team %s: %s", team_id, exc)
        failed_record = AutomationAnalysis(
            team_id=team.id,
            created_by=current_user.id,
            window_days=data.window_days,
            status="failed",
            period_start=period_start,
            period_end=period_end,
            error_message=str(exc)[:1000],
        )
        db.add(failed_record)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AI analysis failed: {str(exc)[:200]}",
        )

    # ── Persist completed result ───────────────────────────────────────────────
    record = AutomationAnalysis(
        team_id=team.id,
        created_by=current_user.id,
        window_days=data.window_days,
        status="completed",
        period_start=period_start,
        period_end=period_end,
        findings_json=json.dumps(llm_result["findings"]),
        summary_text=llm_result.get("summary", ""),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return AutomationAnalysisDetailResponse(
        id=str(record.id),
        team_id=str(record.team_id),
        window_days=record.window_days,
        status=record.status,
        period_start=record.period_start,
        period_end=record.period_end,
        findings=llm_result["findings"],
        summary_text=record.summary_text,
        error_message=None,
        created_at=record.created_at,
    )


# ---------------------------------------------------------------------------
# GET /automation/{team_id}/history/{analysis_id}
# ---------------------------------------------------------------------------

@router.get("/{team_id}/history/{analysis_id}", response_model=AutomationAnalysisDetailResponse)
async def get_analysis_detail(
    team_id: str,
    analysis_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Return full detail (including findings) of a specific analysis run. Starter plan only."""
    team, _ = await require_team_manager(team_id, current_user, db)
    _require_starter(team)

    result = await db.execute(
        select(AutomationAnalysis).where(
            and_(
                AutomationAnalysis.id == analysis_id,
                AutomationAnalysis.team_id == team.id,
            )
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    findings = []
    if record.findings_json:
        try:
            findings = json.loads(record.findings_json)
        except json.JSONDecodeError:
            findings = []

    return AutomationAnalysisDetailResponse(
        id=str(record.id),
        team_id=str(record.team_id),
        window_days=record.window_days,
        status=record.status,
        period_start=record.period_start,
        period_end=record.period_end,
        findings=findings,
        summary_text=record.summary_text,
        error_message=record.error_message,
        created_at=record.created_at,
    )
