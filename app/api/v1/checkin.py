import uuid
import structlog
from datetime import datetime, date, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.core.database import get_db
from app.models import Checkin, CheckinAnswer, TeamQuestion, User, Team, TeamMember, Blocker
from app.schemas import (
    CheckinTokenResponse,
    QuestionItem,
    SubmitCheckinRequest,
    CheckinResponse,
    CheckinAnswerResponse,
    CheckinHistoryItem,
    ConfirmHoursRequest,
)
from app.core.dependencies import get_current_user, require_manager, require_team_access, require_team_manager
from app.jobs.checkin_job import send_daily_emails
from app.services.email_service import send_daily_checkin_email
from app.utils.plan_limits import user_has_starter_access

logger = structlog.get_logger(__name__)
router = APIRouter()


async def _get_checkin_answers(checkin_id, db: AsyncSession) -> list[CheckinAnswerResponse]:
    """Fetch CheckinAnswer rows for a given checkin and join with question labels."""
    rows = await db.execute(
        select(CheckinAnswer, TeamQuestion)
        .join(TeamQuestion, CheckinAnswer.question_id == TeamQuestion.id)
        .where(CheckinAnswer.checkin_id == checkin_id)
        .order_by(TeamQuestion.order_index)
    )
    return [
        CheckinAnswerResponse(
            question_id=str(ca.question_id),
            question_label=q.label,
            answer=ca.answer,
            is_blocker_type=q.is_blocker_type,
        )
        for ca, q in rows.all()
    ]


@router.get("/{team_id}/today", response_model=list[CheckinResponse])
async def today_checkins(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all submitted check-ins for today for a specific team."""
    today = date.today()
    
    team, _ = await require_team_access(team_id, current_user, db)

    rows = await db.execute(
        select(Checkin, User)
        .join(User, Checkin.user_id == User.id)
        .where(
            and_(
                Checkin.team_id == team.id,
                Checkin.date == today,
                Checkin.submitted_at.isnot(None),
            )
        )
    )

    result = []
    for checkin, user in rows.all():
        answers = await _get_checkin_answers(checkin.id, db)
        result.append(
            CheckinResponse(
                id=str(checkin.id),
                user_id=str(checkin.user_id),
                member_name=user.name,
                date=str(checkin.date),
                yesterday=checkin.yesterday,
                today=checkin.today,
                blockers=checkin.blockers,
                answers=answers,
                submitted_at=checkin.submitted_at,
            )
        )
    return result


@router.get("/{team_id}/date/{checkin_date}", response_model=list[CheckinResponse])
async def checkins_by_date(
    team_id: str,
    checkin_date: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all submitted check-ins for a specific date for a team."""
    try:
        target_date = date.fromisoformat(checkin_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    team, _ = await require_team_access(team_id, current_user, db)

    rows = await db.execute(
        select(Checkin, User)
        .join(User, Checkin.user_id == User.id)
        .where(
            and_(
                Checkin.team_id == team.id,
                Checkin.date == target_date,
                Checkin.submitted_at.isnot(None),
            )
        )
    )

    result = []
    for checkin, user in rows.all():
        answers = await _get_checkin_answers(checkin.id, db)
        result.append(
            CheckinResponse(
                id=str(checkin.id),
                user_id=str(checkin.user_id),
                member_name=user.name,
                date=str(checkin.date),
                yesterday=checkin.yesterday,
                today=checkin.today,
                blockers=checkin.blockers,
                answers=answers,
                submitted_at=checkin.submitted_at,
            )
        )
    return result


@router.get("/{team_id}/history/{user_id}", response_model=list[CheckinHistoryItem])
async def checkin_history(
    team_id: str,
    user_id: str,
    days: int = Query(7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return submitted check-ins for a specific member within the last N days.
    Free plan is capped at 7 days; Starter plan allows up to 30 days."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Enforce plan-based history limit using the team manager's account plan
    if team.manager_id and team.manager_id == current_user.id:
        manager = current_user
    else:
        manager = (await db.execute(select(User).where(User.id == team.manager_id))).scalar_one_or_none()
    max_days = 30 if user_has_starter_access(manager) else 7
    days = min(days, max_days)

    # Confirm the member belongs to the team
    member_check = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    )
    if not member_check.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member not in this team")

    since = date.today() - timedelta(days=days - 1)

    rows = await db.execute(
        select(Checkin)
        .where(
            and_(
                Checkin.team_id == team.id,
                Checkin.user_id == user_id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= since,
            )
        )
        .order_by(Checkin.date.desc())
    )

    checkins = rows.scalars().all()
    result = []
    for c in checkins:
        answers = await _get_checkin_answers(c.id, db)
        result.append(
            CheckinHistoryItem(
                date=str(c.date),
                yesterday=c.yesterday,
                today=c.today,
                blockers=c.blockers,
                answers=answers,
                submitted_at=c.submitted_at,
            )
        )
    return result


@router.get("/my-streak")
async def my_streak(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return all submitted check-ins for the current year for the logged-in member."""
    year_start = date(date.today().year, 1, 1)

    rows = await db.execute(
        select(Checkin)
        .where(
            and_(
                Checkin.user_id == current_user.id,
                Checkin.submitted_at.isnot(None),
                Checkin.date >= year_start,
            )
        )
        .order_by(Checkin.date.asc())
    )

    return [
        {
            "date": str(c.date),
            "yesterday": c.yesterday,
            "today": c.today,
            "blockers": c.blockers,
            "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
        }
        for c in rows.scalars().all()
    ]


@router.get("/my-today")
async def my_today_checkin(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current member's check-in record for today (if any)."""
    today = date.today()

    # Find the first team this member belongs to (member may be in multiple teams)
    member_result = await db.execute(
        select(TeamMember).where(TeamMember.user_id == current_user.id).limit(1)
    )
    membership = member_result.scalar_one_or_none()

    team_name = ""
    if membership:
        team_result = await db.execute(select(Team).where(Team.id == membership.team_id))
        team = team_result.scalar_one_or_none()
        team_name = team.name if team else ""

    checkin_result = await db.execute(
        select(Checkin).where(
            and_(
                Checkin.user_id == current_user.id,
                Checkin.date == today,
            )
        )
    )
    checkin = checkin_result.scalar_one_or_none()

    if not checkin:
        return {"team_name": team_name, "submitted_at": None}

    return {
        "team_name": team_name,
        "submitted_at": checkin.submitted_at,
        "yesterday": checkin.yesterday,
        "today": checkin.today,
        "blockers": checkin.blockers,
        "answers": [
            {
                "question_id": a.question_id,
                "question_label": a.question_label,
                "answer": a.answer,
                "is_blocker_type": a.is_blocker_type,
            }
            for a in await _get_checkin_answers(checkin.id, db)
        ],
    }


@router.get("/{token}", response_model=CheckinTokenResponse)
async def get_checkin_by_token(token: str, db: AsyncSession = Depends(get_db)):
    """Validate a magic-link token. Returns member info and the team's questions."""
    result = await db.execute(select(Checkin).where(Checkin.checkin_token == token))
    checkin = result.scalar_one_or_none()

    if not checkin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired check-in link")

    # Check 24-hour expiry
    if datetime.now(timezone.utc).replace(tzinfo=None) > checkin.created_at + timedelta(hours=24):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This check-in link has expired")

    user_result = await db.execute(select(User).where(User.id == checkin.user_id))
    user = user_result.scalar_one_or_none()

    team_result = await db.execute(select(Team).where(Team.id == checkin.team_id))
    team = team_result.scalar_one_or_none()

    # Load enabled questions for the team (ordered)
    from app.api.v1.teams import _ensure_team_questions
    if team:
        await _ensure_team_questions(team, db)
    questions_result = await db.execute(
        select(TeamQuestion)
        .where(and_(TeamQuestion.team_id == checkin.team_id, TeamQuestion.enabled == True))
        .order_by(TeamQuestion.order_index)
    )
    questions = [
        QuestionItem(id=str(q.id), label=q.label, is_blocker_type=q.is_blocker_type, required=not q.is_blocker_type)
        for q in questions_result.scalars().all()
    ]

    # Cost Intelligence: determine if this member needs to confirm their working hours
    tm_result = await db.execute(
        select(TeamMember).where(
            and_(
                TeamMember.team_id == checkin.team_id,
                TeamMember.user_id == checkin.user_id,
                TeamMember.status == "active",
            )
        )
    )
    tm = tm_result.scalar_one_or_none()
    hours_confirmation_needed = bool(
        tm is not None
        and tm.hourly_rate is not None
        and not (tm.hours_confirmed or False)
    )

    return CheckinTokenResponse(
        member_name=user.name if user else "Team Member",
        team_name=team.name if team else "Your Team",
        date=checkin.date.strftime("%A, %B %d %Y"),
        already_submitted=bool(checkin.submitted_at),
        questions=questions,
        hours_confirmation_needed=hours_confirmation_needed,
        hours_per_day=tm.hours_per_day if tm else None,
    )


@router.post("/{token}/confirm-hours", status_code=status.HTTP_200_OK)
async def confirm_checkin_hours(
    token: str,
    data: ConfirmHoursRequest,
    db: AsyncSession = Depends(get_db),
):
    """Let a member confirm or update their working hours per day via the magic-link page.
    Only meaningful when the member has an hourly_rate set and hasn't confirmed hours yet.
    Token must still be within the 24-hour validity window.
    """
    result = await db.execute(select(Checkin).where(Checkin.checkin_token == token))
    checkin = result.scalar_one_or_none()

    if not checkin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired check-in link")

    if datetime.now(timezone.utc).replace(tzinfo=None) > checkin.created_at + timedelta(hours=24):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This check-in link has expired")

    tm_result = await db.execute(
        select(TeamMember).where(
            and_(
                TeamMember.team_id == checkin.team_id,
                TeamMember.user_id == checkin.user_id,
                TeamMember.status == "active",
            )
        )
    )
    tm = tm_result.scalar_one_or_none()
    if not tm:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not an active member of this team",
        )

    tm.hours_per_day = data.hours_per_day
    tm.hours_confirmed = True
    db.add(tm)
    await db.commit()
    return {"message": "Hours confirmed", "hours_per_day": data.hours_per_day}


@router.post("/{token}", status_code=status.HTTP_200_OK)
async def submit_checkin(
    token: str,
    data: SubmitCheckinRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit answers for a magic-link check-in. Auto-creates blocker for flagged questions."""
    result = await db.execute(select(Checkin).where(Checkin.checkin_token == token))
    checkin = result.scalar_one_or_none()

    if not checkin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired check-in link")

    if checkin.submitted_at:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already submitted today's check-in")

    if datetime.now(timezone.utc).replace(tzinfo=None) > checkin.created_at + timedelta(hours=24):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This check-in link has expired")

    # Validate that all submitted question IDs belong to the team
    team_questions_result = await db.execute(
        select(TeamQuestion)
        .where(TeamQuestion.team_id == checkin.team_id)
    )
    team_questions = {str(q.id): q for q in team_questions_result.scalars().all()}

    checkin.token_used = True
    checkin.submitted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(checkin)
    await db.flush()  # ensure checkin.id is available

    # Store each answer and auto-create blockers for flagged questions
    for item in data.answers:
        if item.question_id not in team_questions:
            continue  # silently skip unknown questions
        ca = CheckinAnswer(
            checkin_id=checkin.id,
            question_id=item.question_id,
            answer=item.answer or "",
        )
        db.add(ca)

        q = team_questions[item.question_id]
        if q.is_blocker_type and item.answer and item.answer.strip():
            blocker = Blocker(
                team_id=checkin.team_id,
                user_id=checkin.user_id,
                checkin_id=checkin.id,
                status="open",
                title=item.answer[:255],
                description=item.answer,
            )
            db.add(blocker)

    await db.commit()
    return {"message": "Check-in submitted successfully!"}


@router.post("/internal/send-daily-emails", status_code=status.HTTP_200_OK)
async def trigger_daily_emails():
    """Manual trigger for the daily email job."""
    try:
        await send_daily_emails()
        return {"message": "Daily check-in emails sent successfully"}
    except Exception as e:
        logger.error("Manual email trigger failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/{team_id}/send-now/all", status_code=status.HTTP_200_OK)
async def send_checkin_to_all_members(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Manager sends a check-in email to all active members."""
    team, _ = await require_team_manager(team_id, current_user, db)

    today = date.today()
    date_str = today.strftime("%A, %B %d %Y")

    members_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.status == "active"))
    )
    rows = members_result.all()

    if not rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active members in the team")

    sent, skipped, failed = 0, 0, []
    for tm, user in rows:
        # Reuse existing checkin row if already created today
        existing = await db.execute(
            select(Checkin).where(
                and_(Checkin.team_id == team.id, Checkin.user_id == user.id, Checkin.date == today)
            )
        )
        checkin = existing.scalar_one_or_none()
        if checkin and checkin.submitted_at:
            skipped += 1
            continue

        if not checkin:
            token = str(uuid.uuid4())
            checkin = Checkin(team_id=team.id, user_id=user.id, date=today, checkin_token=token)
            db.add(checkin)
            await db.flush()

        try:
            send_daily_checkin_email(
                to_email=user.email,
                member_name=user.name or user.email,
                team_name=team.name,
                checkin_token=checkin.checkin_token,
                date_str=date_str,
            )
            sent += 1
        except Exception as e:
            logger.error("Failed to send check-in to %s: %s", user.email, e)
            failed.append({"email": user.email, "error": str(e)})

    await db.commit()
    return {
        "message": f"Check-in emails sent to {sent} member(s).",
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }


@router.post("/{team_id}/send-now/{user_id}", status_code=status.HTTP_200_OK)
async def send_checkin_to_member(
    team_id: str,
    user_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Manager sends a check-in email to a specific member."""
    team, _ = await require_team_manager(team_id, current_user, db)

    # Confirm member belongs to this team and is active
    member_result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id, TeamMember.status == "active"))
    )
    row = member_result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active member not found in this team")

    _, user = row
    today = date.today()
    date_str = today.strftime("%A, %B %d %Y")

    # Check if already submitted
    existing = await db.execute(
        select(Checkin).where(
            and_(Checkin.team_id == team.id, Checkin.user_id == user_id, Checkin.date == today)
        )
    )
    checkin = existing.scalar_one_or_none()

    if checkin and checkin.submitted_at:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This member has already submitted today's check-in",
        )

    if not checkin:
        token = str(uuid.uuid4())
        checkin = Checkin(team_id=team.id, user_id=user_id, date=today, checkin_token=token)
        db.add(checkin)
        await db.flush()

    try:
        send_daily_checkin_email(
            to_email=user.email,
            member_name=user.name or user.email,
            team_name=team.name,
            checkin_token=checkin.checkin_token,
            date_str=date_str,
        )
        await db.commit()
        return {"message": f"Check-in email sent to {user.name}"}
    except Exception as e:
        logger.error("Failed to send check-in to %s: %s", user.email, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send email: {str(e)}",
        )
