from datetime import datetime, timezone

import structlog
from sqlalchemy import select, and_

from app.core.database import AsyncSessionLocal
from app.models.automation import AutomationSchedule
from app.models.team import Team
from app.services.ai_task_radar_service import compute_next_run_at, run_team_analysis

logger = structlog.get_logger(__name__)


async def run_due_ai_task_radar() -> None:
    now_utc = datetime.now(timezone.utc)
    now_naive = now_utc.replace(tzinfo=None)

    async with AsyncSessionLocal() as db:
        due_schedules = (await db.execute(
            select(AutomationSchedule).where(
                and_(
                    AutomationSchedule.enabled == True,  # noqa: E712
                    AutomationSchedule.next_run_at.isnot(None),
                    AutomationSchedule.next_run_at <= now_naive,
                )
            )
        )).scalars().all()

    if not due_schedules:
        return

    logger.info("ai_radar_job.due", count=len(due_schedules))

    for sched in due_schedules:
        async with AsyncSessionLocal() as db:
            team = (await db.execute(select(Team).where(Team.id == sched.team_id))).scalar_one_or_none()
            if team is None:
                logger.warning("ai_radar_job.team_missing", team_id=str(sched.team_id))
                continue

            try:
                await run_team_analysis(db, team, window_days=7, trigger="scheduled", created_by_user_id=team.manager_id)
                logger.info("ai_radar_job.completed", team_id=str(team.id))
            except Exception as exc:
                logger.exception("ai_radar_job.failed", team_id=str(team.id), error=str(exc))

            # Advance next_run_at regardless of success/failure
            sched_fresh = (await db.execute(select(AutomationSchedule).where(AutomationSchedule.team_id == team.id))).scalar_one_or_none()
            if sched_fresh:
                sched_fresh.last_run_at = now_utc.replace(tzinfo=None)
                next_run = compute_next_run_at(sched_fresh, now_utc=now_utc)
                sched_fresh.next_run_at = next_run.replace(tzinfo=None)
                await db.commit()
