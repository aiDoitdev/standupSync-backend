import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select, and_

from app.core.database import AsyncSessionLocal
from app.models.checkin import Checkin
from app.models.team import Team, TeamMember
from app.models.user import User
from app.services.email_service import send_daily_checkin_email

logger = structlog.get_logger(__name__)


def _send_time_reached(send_time: str, timezone_str: str) -> bool:
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
    today = date.today()
    logger.info("checkin_job.start", date=str(today))

    async with AsyncSessionLocal() as db:
        teams = (await db.execute(select(Team))).scalars().all()

        for team in teams:
            rows = (await db.execute(
                select(TeamMember, User)
                .join(User, TeamMember.user_id == User.id)
                .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
            )).all()

            for tm, user in rows:
                member_tz = tm.timezone or "Asia/Kolkata"
                member_send_time = tm.send_time or "09:00"

                if not _send_time_reached(member_send_time, member_tz):
                    continue

                existing = (await db.execute(
                    select(Checkin).where(and_(Checkin.team_id == team.id, Checkin.user_id == user.id, Checkin.date == today))
                )).scalar_one_or_none()

                if existing:
                    logger.info("checkin_job.already_exists", email=user.email, date=str(today))
                    continue

                checkin_token = str(uuid.uuid4())
                db.add(Checkin(team_id=team.id, user_id=user.id, date=today, checkin_token=checkin_token))
                await db.flush()

                try:
                    send_daily_checkin_email(
                        to_email=user.email,
                        member_name=user.name or user.email,
                        team_name=team.name,
                        checkin_token=checkin_token,
                        date_str=today.strftime("%A, %B %d %Y"),
                    )
                    logger.info("checkin_job.email_sent", email=user.email, tz=member_tz)
                except Exception as exc:
                    logger.error("checkin_job.email_failed", email=user.email, error=str(exc))

        await db.commit()
    logger.info("checkin_job.done")
