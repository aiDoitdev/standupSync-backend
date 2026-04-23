"""
AI Task Radar orchestrator — aggregates check-in data, calls LLM, persists results.
"""
from __future__ import annotations

import calendar
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.automation import AutomationAnalysis, AutomationSchedule, AutomationTask
from app.models.checkin import Checkin, CheckinAnswer
from app.models.team import TeamMember, TeamQuestion
from app.models.user import User
from app.services.llm_service import generate_ai_task_radar

logger = structlog.get_logger(__name__)

_PHRASE_SPLIT = re.compile(r"[.;,\n]|(?:\s+and\s+)|(?:\s+then\s+)")


# ── Phrase aggregation ─────────────────────────────────────────────────────────

def _tokenize_phrases(text: str) -> list[str]:
    parts = _PHRASE_SPLIT.split(text.lower())
    phrases: list[str] = []
    for part in parts:
        words = part.strip().split()
        if 3 <= len(words) <= 8:
            phrases.append(" ".join(words))
        elif len(words) > 8:
            for i in range(len(words) - 3):
                phrases.append(" ".join(words[i: i + 4]))
    return phrases


def _top_phrases(answers: list[str], limit: int = 8) -> list[str]:
    pool: list[str] = []
    for ans in answers:
        if ans and ans.strip():
            pool.extend(_tokenize_phrases(ans))
    counter = Counter(pool)
    repeated = [p for p, c in counter.most_common(limit) if c > 1]
    if repeated:
        return repeated
    seen: set[str] = set()
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


# ── Schedule helpers ───────────────────────────────────────────────────────────

def _zone(tz_str: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("Asia/Kolkata")


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> Optional[date]:
    first_weekday, days_in_month = calendar.monthrange(year, month)
    first_occurrence_day = 1 + ((weekday - first_weekday) % 7)
    day = first_occurrence_day + (n - 1) * 7
    return date(year, month, day) if day <= days_in_month else None


def compute_next_run_at(
    schedule: AutomationSchedule,
    now_utc: Optional[datetime] = None,
) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    tz = _zone(schedule.timezone)
    local_now = now_utc.astimezone(tz)
    hh, mm = (schedule.run_time or "08:00").split(":")
    run_local_time = time(hour=int(hh), minute=int(mm))
    target_weekday = schedule.day_of_week

    if schedule.cadence == "monthly":
        wom = schedule.week_of_month or 1
        for month_offset in range(4):
            y = local_now.year + ((local_now.month - 1 + month_offset) // 12)
            m = ((local_now.month - 1 + month_offset) % 12) + 1
            d = _nth_weekday_of_month(y, m, target_weekday, wom)
            if d is None:
                continue
            candidate = datetime.combine(d, run_local_time, tzinfo=tz)
            if candidate > local_now:
                return candidate.astimezone(timezone.utc)

    days_ahead = (target_weekday - local_now.weekday()) % 7
    candidate = datetime.combine(
        local_now.date() + timedelta(days=days_ahead), run_local_time, tzinfo=tz
    )
    if candidate <= local_now:
        candidate += timedelta(days=7)

    if schedule.cadence == "biweekly" and schedule.last_run_at is not None:
        last_local = schedule.last_run_at.astimezone(tz)
        while (candidate - last_local) < timedelta(days=14):
            candidate += timedelta(days=7)

    return candidate.astimezone(timezone.utc)


# ── Main orchestrator ──────────────────────────────────────────────────────────

async def run_team_analysis(
    db: AsyncSession,
    team,
    *,
    window_days: int,
    trigger: str,
    created_by_user_id: uuid.UUID,
) -> AutomationAnalysis:
    today = date.today()
    period_end = today
    period_start = today - timedelta(days=window_days - 1)

    members_rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    member_tuples = members_rows.all()

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

    members_payload: list[dict] = [
        {
            "user_id": str(user.id),
            "name": user.name or user.email,
            "phrases": _top_phrases(per_user_answers.get(str(user.id), []), limit=8),
        }
        for tm, user in member_tuples
    ]

    is_empty = sum(len(m["phrases"]) for m in members_payload) == 0

    if is_empty:
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
        try:
            from app.services.email_service import send_ai_task_radar_empty_email
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
            logger.warning("ai_radar.empty_email_failed", team_id=str(team.id), error=str(exc))
        return record

    try:
        llm_result = await generate_ai_task_radar(team.name, members_payload, window_days)
    except Exception as exc:
        logger.exception("ai_radar.llm_failed", team_id=str(team.id))
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

    name_to_user_id: dict[str, uuid.UUID] = {
        (user.name or user.email).strip().lower(): user.id
        for _tm, user in member_tuples
    }

    llm_members = llm_result.get("members") or []
    all_tasks_flat: list[dict] = []
    total_tasks = 0

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
        llm_response_json=llm_result,
    )
    db.add(record)
    await db.flush()

    for m in llm_members:
        resolved_uid: Optional[uuid.UUID] = None
        raw_uid = m.get("user_id")
        if raw_uid:
            try:
                resolved_uid = uuid.UUID(str(raw_uid))
            except (ValueError, TypeError):
                pass
        if resolved_uid is None:
            resolved_uid = name_to_user_id.get((m.get("name") or "").strip().lower())

        for task in (m.get("tasks") or []):
            score = int(task.get("automation_score") or 0)
            tier = task.get("tier") or "P3"
            db.add(AutomationTask(
                analysis_id=record.id,
                user_id=resolved_uid,
                assigned_name=m.get("name"),
                task_title=(task.get("task_title") or "Untitled task")[:500],
                task_description=task.get("task_description"),
                automation_score=score,
                tier=tier,
                suggested_tools_json=task.get("suggested_tools") or [],
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

    try:
        from app.services.email_service import send_team_report_email
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
        logger.warning("ai_radar.report_email_failed", team_id=str(team.id), error=str(exc))

    return record
