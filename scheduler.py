import uuid
import logging
import os
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, and_
import httpx
import structlog

from database import AsyncSessionLocal
from models import (
    AutomationAnalysis,
    AutomationSchedule,
    Checkin,
    Subscription,
    Team,
    TeamMember,
    User,
)
from email_service import send_daily_checkin_email

logger = structlog.get_logger(__name__)
scheduler = AsyncIOScheduler()


def _member_send_time_reached(send_time: str, timezone_str: str) -> bool:
    """Return True if the current minute matches the member's configured send_time in their timezone."""
    try:
        tz = ZoneInfo(timezone_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("Asia/Kolkata")
    local_now = datetime.now(tz)
    try:
        hh, mm = send_time.split(":")
        return local_now.hour == int(hh) and local_now.minute == int(mm)
    except Exception:
        return False


async def send_daily_emails() -> None:
    """
    Per-member scheduled job (runs every minute):
      1. Query all active teams.
      2. For each active member check whether current local time == their send_time.
      3. If yes — create a checkin row (with unique token) if one doesn't exist, then send the email.
    """
    today = date.today()
    logger.info("Running per-member check-in job for %s", today)

    async with AsyncSessionLocal() as db:
        teams_result = await db.execute(select(Team))
        teams = teams_result.scalars().all()

        for team in teams:
            members_result = await db.execute(
                select(TeamMember, User)
                .join(User, TeamMember.user_id == User.id)
                .where(
                    and_(
                        TeamMember.team_id == team.id,
                        TeamMember.status == "active",
                    )
                )
            )
            rows = members_result.all()

            for team_member, user in rows:
                member_tz = team_member.timezone or "Asia/Kolkata"
                member_send_time = team_member.send_time or "09:00"

                if not _member_send_time_reached(member_send_time, member_tz):
                    continue

                existing_result = await db.execute(
                    select(Checkin).where(
                        and_(
                            Checkin.team_id == team.id,
                            Checkin.user_id == user.id,
                            Checkin.date == today,
                        )
                    )
                )
                if existing_result.scalar_one_or_none():
                    logger.info("Checkin row already exists for %s on %s — skipping", user.email, today)
                    continue

                checkin_token = str(uuid.uuid4())
                checkin = Checkin(
                    team_id=team.id,
                    user_id=user.id,
                    date=today,
                    checkin_token=checkin_token,
                )
                db.add(checkin)
                await db.flush()

                date_str = today.strftime("%A, %B %d %Y")
                try:
                    send_daily_checkin_email(
                        to_email=user.email,
                        member_name=user.name or user.email,
                        team_name=team.name,
                        checkin_token=checkin_token,
                        date_str=date_str,
                    )
                    logger.info(
                        "Sent check-in email to %s (tz=%s, send_time=%s)",
                        user.email, member_tz, member_send_time,
                    )
                except Exception as e:
                    logger.error("Failed to send check-in email to %s: %s", user.email, e)

        await db.commit()
    logger.info("Per-member check-in job completed")


async def run_due_ai_task_radar() -> None:
    """
    Poll AutomationSchedule for rows where enabled=TRUE and next_run_at <= NOW() (UTC).
    Each due schedule triggers one Ai Task Radar analysis, then next_run_at is advanced.

    Safety:
      * We process schedules sequentially to bound concurrent LLM spend.
      * Each run uses its own DB session so one failure doesn't poison others.
      * UNIQUE(team_id, period_start) on automation_analyses is the final guard against
        duplicates if two workers ever race.
    """
    from ai_task_radar_service import compute_next_run_at, run_team_analysis

    now_utc = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        due_result = await db.execute(
            select(AutomationSchedule).where(
                and_(
                    AutomationSchedule.enabled == True,  # noqa: E712
                    AutomationSchedule.next_run_at.isnot(None),
                    AutomationSchedule.next_run_at <= now_utc,
                )
            )
        )
        due_schedules = due_result.scalars().all()

    if not due_schedules:
        return

    logger.info("Ai Task Radar scheduler: %s team(s) due", len(due_schedules))

    for sched in due_schedules:
        async with AsyncSessionLocal() as db:
            team_result = await db.execute(select(Team).where(Team.id == sched.team_id))
            team = team_result.scalar_one_or_none()
            if team is None:
                logger.warning("Ai Task Radar scheduler: team %s missing — skipping", sched.team_id)
                continue
            # Manager owns the analysis by default.
            manager_id = team.manager_id

            try:
                await run_team_analysis(
                    db,
                    team,
                    window_days=7,
                    trigger="scheduled",
                    created_by_user_id=manager_id,
                )
                logger.info("Ai Task Radar scheduler: team %s analysis completed", team.id)
            except Exception as exc:
                logger.exception("Ai Task Radar scheduler: team %s analysis failed: %s", team.id, exc)

            # Advance next_run_at whether the run succeeded or failed — failures will
            # surface via the UI; we don't want to tight-loop a broken team.
            sched_result = await db.execute(
                select(AutomationSchedule).where(AutomationSchedule.team_id == team.id)
            )
            sched_fresh = sched_result.scalar_one_or_none()
            if sched_fresh is None:
                continue
            sched_fresh.last_run_at = now_utc.replace(tzinfo=None)
            next_run = compute_next_run_at(sched_fresh, now_utc=now_utc)
            sched_fresh.next_run_at = next_run.replace(tzinfo=None)
            await db.commit()


async def reconcile_subscriptions() -> None:
    """
    Every 6 hours: sync subscription status from Lemon Squeezy API.

    Catches missed webhooks — if a subscription is cancelled/expired in LS
    but our DB still shows 'starter/active', this job corrects it.
    Also expires grace periods whose plan_expires_at has passed.
    """
    from routers.billing import _ls_headers, _parse_ls_datetime, LS_API_BASE

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    async with AsyncSessionLocal() as db:
        # ── 1. Expire grace periods that have passed ──────────────────────────
        grace_result = await db.execute(
            select(Team).where(
                and_(
                    Team.plan_status == "canceled",
                    Team.plan_expires_at.isnot(None),
                    Team.plan_expires_at <= now_utc,
                )
            )
        )
        for team in grace_result.scalars().all():
            team.plan             = "free"
            team.plan_expires_at  = None
            db.add(team)
            logger.info("reconcile.grace_period_expired", team_id=str(team.id))

        # ── 2. Re-check active Starter subscriptions against LS API ──────────
        active_result = await db.execute(
            select(Team).where(
                and_(Team.plan == "starter", Team.ls_subscription_id.isnot(None))
            )
        )
        active_teams = active_result.scalars().all()

    for team in active_teams:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{LS_API_BASE}/subscriptions/{team.ls_subscription_id}",
                    headers=_ls_headers(),
                    timeout=10.0,
                )
            if resp.status_code != 200:
                continue

            attrs      = resp.json()["data"]["attributes"]
            ls_status  = attrs.get("status", "")
            period_end = _parse_ls_datetime(
                attrs.get("current_period_end") or attrs.get("ends_at") or attrs.get("renews_at")
            )

            if ls_status in ("cancelled", "expired") and team.plan_status == "active":
                async with AsyncSessionLocal() as db:
                    fresh = await db.execute(select(Team).where(Team.id == team.id))
                    t = fresh.scalar_one_or_none()
                    if t:
                        t.plan_status      = "canceled"
                        t.plan_expires_at  = period_end
                        t.ls_subscription_id = None
                        db.add(t)
                        db.add(Subscription(
                            team_id=t.id,
                            ls_subscription_id=team.ls_subscription_id,
                            plan="starter", status="canceled",
                            current_period_end=period_end,
                            canceled_at=now_utc,
                        ))
                        await db.commit()
                        logger.info(
                            "reconcile.subscription_corrected",
                            team_id=str(t.id), ls_status=ls_status,
                        )
        except Exception as exc:
            logger.warning("reconcile.team_check_failed", team_id=str(team.id), error=str(exc))

    async with AsyncSessionLocal() as db:
        await db.commit()

    logger.info("reconcile.done", checked=len(active_teams))


def start_scheduler() -> None:
    """Register all background jobs."""
    scheduler.add_job(
        send_daily_emails,
        CronTrigger(minute="*"),
        id="daily_checkin_emails",
        replace_existing=True,
    )
    scheduler.add_job(
        run_due_ai_task_radar,
        CronTrigger(minute="*/10"),
        id="ai_task_radar_poller",
        replace_existing=True,
    )
    scheduler.add_job(
        reconcile_subscriptions,
        CronTrigger(hour="*/6"),   # runs every 6 hours
        id="subscription_reconciler",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started — checkin emails (1 min), AI radar (10 min), subscription reconciler (6 h)")
