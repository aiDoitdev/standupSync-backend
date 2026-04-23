"""
Ai Task Radar — orchestrator (single LLM call per team per scheduled run).

Responsibilities:
  * aggregate recent check-in answers per-member
  * hand the aggregated payload to generate_ai_task_radar()
  * persist one AutomationAnalysis row + N AutomationTask rows
  * mark runs with empty input as is_empty=True and trigger an email nudge
  * compute next_run_at (UTC) from an AutomationSchedule row

The public entry points (run_team_analysis, compute_next_run_at) are intentionally
pure-async so the same function is reused by the scheduler job and the HTTP routes.
"""
from __future__ import annotations

import calendar
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models import (
    AutomationAnalysis,
    AutomationSchedule,
    AutomationTask,
    Checkin,
    CheckinAnswer,
    Team,
    TeamMember,
    TeamQuestion,
    User,
)
from llm_service import generate_ai_task_radar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

_PHRASE_SPLIT = re.compile(r"[.;,\n]|(?:\s+and\s+)|(?:\s+then\s+)")


def _tokenize_phrases(text: str) -> list[str]:
    parts = _PHRASE_SPLIT.split(text.lower())
    phrases: list[str] = []
    for part in parts:
        words = part.strip().split()
        if 3 <= len(words) <= 8:
            phrases.append(" ".join(words))
        elif len(words) > 8:
            for i in range(len(words) - 3):
                phrases.append(" ".join(words[i : i + 4]))
    return phrases


def _top_phrases(answers: list[str], limit: int = 8) -> list[str]:
    """Return the top-N most repeated phrases across a member's answers, falling back
    to deduped raw answers when nothing is 'repeated'."""
    pool: list[str] = []
    for ans in answers:
        if ans and ans.strip():
            pool.extend(_tokenize_phrases(ans))
    counter = Counter(pool)
    repeated = [p for p, c in counter.most_common(limit) if c > 1]
    if repeated:
        return repeated
    # No repeated patterns — fall back to unique condensed answer snippets
    seen = set()
    fallback: list[str] = []
    for ans in answers:
        if not ans:
            continue
        snippet = ans.strip()
        if len(snippet) > 140:
            snippet = snippet[:140].rstrip() + "…"
        key = snippet.lower()
        if key and key not in seen:
            seen.add(key)
            fallback.append(snippet)
        if len(fallback) >= limit:
            break
    return fallback


# ---------------------------------------------------------------------------
# next_run_at computation
# ---------------------------------------------------------------------------

def _zone(tz_str: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("Asia/Kolkata")


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> Optional[date]:
    """Return the nth (1..4) weekday of a given month, or None if it doesn't exist."""
    first_weekday, days_in_month = calendar.monthrange(year, month)
    first_occurrence_day = 1 + ((weekday - first_weekday) % 7)
    day = first_occurrence_day + (n - 1) * 7
    if day > days_in_month:
        return None
    return date(year, month, day)


def compute_next_run_at(schedule: AutomationSchedule, now_utc: Optional[datetime] = None) -> datetime:
    """
    Return the next UTC firing time strictly after `now_utc` based on the schedule's
    cadence / day_of_week / week_of_month / run_time / timezone.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = _zone(schedule.timezone)
    local_now = now_utc.astimezone(tz)

    hh, mm = (schedule.run_time or "08:00").split(":")
    run_h, run_m = int(hh), int(mm)
    run_local_time = time(hour=run_h, minute=run_m)
    target_weekday = schedule.day_of_week

    if schedule.cadence == "monthly":
        wom = schedule.week_of_month or 1
        for month_offset in range(0, 4):
            y = local_now.year + ((local_now.month - 1 + month_offset) // 12)
            m = ((local_now.month - 1 + month_offset) % 12) + 1
            d = _nth_weekday_of_month(y, m, target_weekday, wom)
            if d is None:
                continue
            candidate_local = datetime.combine(d, run_local_time, tzinfo=tz)
            if candidate_local > local_now:
                return candidate_local.astimezone(timezone.utc)
        # Unreachable in practice — fall through to weekly logic.

    # weekly / biweekly — walk forward to the next matching weekday.
    days_ahead = (target_weekday - local_now.weekday()) % 7
    candidate_local = datetime.combine(local_now.date() + timedelta(days=days_ahead), run_local_time, tzinfo=tz)
    if candidate_local <= local_now:
        candidate_local += timedelta(days=7)

    if schedule.cadence == "biweekly":
        # Anchor biweekly cadence on last_run_at; if it's set and candidate is within
        # 7 days of the last run, skip another week.
        if schedule.last_run_at is not None:
            last_local = schedule.last_run_at.astimezone(tz)
            while (candidate_local - last_local) < timedelta(days=14):
                candidate_local += timedelta(days=7)

    return candidate_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_team_analysis(
    db: AsyncSession,
    team: Team,
    *,
    window_days: int,
    trigger: str,
    created_by_user_id: uuid.UUID,
) -> AutomationAnalysis:
    """
    Execute a single Ai Task Radar analysis for `team`. Writes:
      * one AutomationAnalysis row (status='completed' | 'failed', is_empty when applicable)
      * N AutomationTask rows for each task returned by the LLM

    Idempotency is the caller's responsibility — the scheduler advances next_run_at
    after every run (success or failure) so a due schedule is never re-processed in
    the same polling tick.
    """
    today = date.today()
    period_end = today
    period_start = today - timedelta(days=window_days - 1)
    period_start_dt = datetime.combine(period_start, datetime.min.time())

    # ── Load active members ──────────────────────────────────────────────────
    members_rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    member_tuples = members_rows.all()

    # ── Load answers for the window (non-blocker questions only) ──────────────
    answers_rows = await db.execute(
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
    per_user_answers: dict[str, list[str]] = defaultdict(list)
    for ca, _tq, user, _ck in answers_rows.all():
        if ca.answer and ca.answer.strip():
            per_user_answers[str(user.id)].append(ca.answer)

    # ── Build payload for the LLM (members always included, even if empty) ─────
    members_payload: list[dict] = []
    for tm, user in member_tuples:
        phrases = _top_phrases(per_user_answers.get(str(user.id), []), limit=8)
        members_payload.append({
            "user_id": str(user.id),
            "name": user.name or user.email,
            "phrases": phrases,
        })

    total_phrases = sum(len(m["phrases"]) for m in members_payload)
    is_empty = total_phrases == 0

    # ── Empty-data short-circuit: persist a no-data record, return immediately ─
    if is_empty:
        logger.info("Ai Task Radar: team %s has no data in last %s days — persisting empty run", team.id, window_days)
        record = AutomationAnalysis(
            team_id=team.id,
            created_by=created_by_user_id,
            window_days=window_days,
            status="completed",
            period_start=period_start,
            period_end=period_end,
            summary_text="No check-in data was submitted in this window.",
            trigger=trigger,
            team_score=0,
            member_count=len(members_payload),
            task_count=0,
            is_empty=True,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        # Email nudge to manager (best-effort; never fails the run)
        try:
            from email_service import send_ai_task_radar_empty_email
            manager_result = await db.execute(select(User).where(User.id == team.manager_id))
            manager = manager_result.scalar_one_or_none()
            if manager and manager.email:
                send_ai_task_radar_empty_email(
                    manager_email=manager.email,
                    manager_name=manager.name or manager.email,
                    team_name=team.name,
                    team_id=str(team.id),
                    window_days=window_days,
                )
        except Exception as exc:
            logger.warning("Failed to send Ai Task Radar empty-data email for team %s: %s", team.id, exc)
        return record

    # ── Call LLM (single nested call) ─────────────────────────────────────────
    try:
        llm_result = await generate_ai_task_radar(team.name, members_payload, window_days)
    except Exception as exc:
        logger.exception("Ai Task Radar LLM call failed for team %s", team.id)
        failed = AutomationAnalysis(
            team_id=team.id,
            created_by=created_by_user_id,
            window_days=window_days,
            status="failed",
            period_start=period_start,
            period_end=period_end,
            error_message=str(exc)[:1000],
            trigger=trigger,
            is_empty=False,
        )
        db.add(failed)
        await db.commit()
        await db.refresh(failed)
        raise

    # ── Persist analysis + tasks in one transaction ───────────────────────────
    name_to_user_id: dict[str, uuid.UUID] = {}
    for tm, user in member_tuples:
        name_to_user_id[(user.name or user.email).strip().lower()] = user.id

    llm_members = llm_result.get("members", []) or []
    total_tasks = 0
    all_tasks_flat: list[dict] = []  # collected for report email

    record = AutomationAnalysis(
        team_id=team.id,
        created_by=created_by_user_id,
        window_days=window_days,
        status="completed",
        period_start=period_start,
        period_end=period_end,
        summary_text=(llm_result.get("summary") or "")[:8000],
        trigger=trigger,
        team_score=int(llm_result.get("team_score") or 0),
        member_count=len(llm_members),
        is_empty=False,
        llm_response_json=json.dumps(llm_result),
    )
    db.add(record)
    await db.flush()  # get record.id

    for m in llm_members:
        resolved_user_id: Optional[uuid.UUID] = None
        raw_uid = m.get("user_id")
        if raw_uid:
            try:
                resolved_user_id = uuid.UUID(str(raw_uid))
            except (ValueError, TypeError):
                resolved_user_id = None
        if resolved_user_id is None:
            resolved_user_id = name_to_user_id.get((m.get("name") or "").strip().lower())

        for task in m.get("tasks", []) or []:
            score = int(task.get("automation_score") or 0)
            tier = task.get("tier") or "P3"
            db.add(AutomationTask(
                analysis_id=record.id,
                user_id=resolved_user_id,
                assigned_name=(m.get("name") or None),
                task_title=(task.get("task_title") or "Untitled task")[:500],
                task_description=task.get("task_description"),
                automation_score=score,
                tier=tier,
                suggested_tools_json=json.dumps(task.get("suggested_tools") or []),
                suggested_workflow=task.get("suggested_workflow"),
                general_suggestion=task.get("general_suggestion"),
                source="checkin",
            ))
            all_tasks_flat.append({
                "title": (task.get("task_title") or "Untitled task")[:100],
                "score": score,
                "tier": tier,
                "assigned_name": m.get("name") or "",
            })
            total_tasks += 1

    record.task_count = total_tasks
    await db.commit()
    await db.refresh(record)

    # Report email to team owner — best-effort, never fails the run.
    try:
        from email_service import send_team_report_email
        manager_result = await db.execute(select(User).where(User.id == team.manager_id))
        manager = manager_result.scalar_one_or_none()
        if manager and manager.email:
            top_tasks = sorted(all_tasks_flat, key=lambda t: -t["score"])[:3]
            send_team_report_email(
                manager_email=manager.email,
                manager_name=manager.name or manager.email,
                team_name=team.name,
                team_id=str(team.id),
                analysis_id=str(record.id),
                team_score=record.team_score or 0,
                period_start=period_start,
                period_end=period_end,
                top_tasks=top_tasks,
                summary_text=record.summary_text or "",
                member_count=record.member_count or 0,
                task_count=record.task_count or 0,
            )
    except Exception as exc:
        logger.warning("Failed to send team report email for team %s: %s", team.id, exc)

    return record
