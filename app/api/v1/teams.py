import uuid
import structlog
from datetime import datetime, date, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_manager, require_team_access, require_team_manager
from app.models.team import Team, TeamMember, TeamQuestion, Invite
from app.models.user import User
from app.models.checkin import Checkin
from app.schemas.team import (
    CreateTeamRequest, TeamResponse, TeamDetailResponse, UserTeamsResponse,
    InviteMembersRequest, MemberStatusResponse, TeamMemberDetailResponse,
    TeamQuestionResponse, TeamQuestionCreateRequest, TeamQuestionUpdateRequest,
    UpdateMemberRequest, PendingInviteResponse,
)
from app.services.email_service import send_invite_email
from app.utils.plan_limits import FREE_MEMBER_LIMIT, user_has_starter_access, require_starter

logger = structlog.get_logger(__name__)
router = APIRouter()


async def _get_team_manager(team: Team, current_user: User, db: AsyncSession) -> User | None:
    """Return the team's manager User, reusing current_user when they are the manager."""
    if team.manager_id and team.manager_id == current_user.id:
        return current_user
    if team.manager_id:
        return (await db.execute(select(User).where(User.id == team.manager_id))).scalar_one_or_none()
    return None


async def _ensure_team_questions(team: Team, db: AsyncSession) -> None:
    try:
        existing = await db.execute(select(TeamQuestion).where(TeamQuestion.team_id == team.id).limit(1))
        if existing.scalar_one_or_none():
            return
        seeds = [
            TeamQuestion(team_id=team.id, order_index=0, label=team.q1_label or "What did you accomplish yesterday?", enabled=True, is_blocker_type=False),
            TeamQuestion(team_id=team.id, order_index=1, label=team.q2_label or "What will you work on today?", enabled=True, is_blocker_type=False),
            TeamQuestion(team_id=team.id, order_index=2, label=team.q3_label or "Any blockers or issues?", enabled=True, is_blocker_type=True),
        ]
        for q in seeds:
            db.add(q)
        await db.commit()
    except Exception:
        logger.exception("teams.seed_questions_failed", team_id=str(team.id))
        await db.rollback()


@router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: CreateTeamRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    existing_count = (await db.execute(
        select(func.count(Team.id)).where(Team.manager_id == current_user.id)
    )).scalar() or 0

    if existing_count >= 1 and not user_has_starter_access(current_user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Free plan allows only 1 team. Upgrade to Starter for unlimited teams.",
        )

    team = Team(name=data.name, manager_id=current_user.id, team_type=data.team_type)
    db.add(team)
    await db.flush()

    for idx, (label, is_blocker) in enumerate([
        ("What did you accomplish yesterday?", False),
        ("What will you work on today?", False),
        ("Any blockers or issues?", True),
    ]):
        db.add(TeamQuestion(team_id=team.id, order_index=idx, label=label, enabled=True, is_blocker_type=is_blocker))

    await db.commit()
    await db.refresh(team)
    return TeamResponse(id=str(team.id), name=team.name, plan=current_user.plan, member_count=0)


@router.get("/", response_model=list[UserTeamsResponse])
async def get_all_teams(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teams_list = []
    if current_user.role == "manager":
        managed = (await db.execute(select(Team).where(Team.manager_id == current_user.id))).scalars().all()
        for team in managed:
            mc = (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id))).scalar() or 0
            teams_list.append(UserTeamsResponse(
                id=str(team.id), name=team.name, user_role="owner", member_count=mc,
                plan=current_user.plan, plan_status=current_user.plan_status,
                created_at=team.created_at, team_type=team.team_type,
            ))

    # Joined teams — fetch manager's plan via a join to avoid N+1
    member_rows = (await db.execute(
        select(TeamMember, Team, User)
        .join(Team, TeamMember.team_id == Team.id)
        .join(User, Team.manager_id == User.id)
        .where(and_(TeamMember.user_id == current_user.id, TeamMember.status == "active"))
    )).all()
    for tm, team, manager in member_rows:
        mc = (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id))).scalar() or 0
        teams_list.append(UserTeamsResponse(
            id=str(team.id), name=team.name, user_role="member", member_count=mc,
            plan=manager.plan, plan_status=manager.plan_status,
            created_at=team.created_at, team_type=team.team_type,
        ))

    return teams_list


@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team_details(team_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_access(team_id, current_user, db)
    await _ensure_team_questions(team, db)
    manager = await _get_team_manager(team, current_user, db)
    mc = (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id))).scalar() or 0
    return TeamDetailResponse(id=str(team.id), name=team.name, plan=manager.plan if manager else "free", member_count=mc, created_at=team.created_at, team_type=team.team_type)


@router.put("/{team_id}", response_model=TeamDetailResponse)
async def update_team(team_id: str, data: CreateTeamRequest, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    team.name = data.name
    team.team_type = data.team_type
    db.add(team)
    await db.commit()
    await db.refresh(team)
    mc = (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id))).scalar() or 0
    return TeamDetailResponse(id=str(team.id), name=team.name, plan=current_user.plan, member_count=mc, created_at=team.created_at, team_type=team.team_type)


@router.get("/{team_id}/questions", response_model=list[TeamQuestionResponse])
async def list_questions(team_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_access(team_id, current_user, db)
    await _ensure_team_questions(team, db)
    result = await db.execute(select(TeamQuestion).where(TeamQuestion.team_id == team.id).order_by(TeamQuestion.order_index))
    return [TeamQuestionResponse(id=str(q.id), team_id=str(q.team_id), order_index=q.order_index, label=q.label, enabled=q.enabled, is_blocker_type=q.is_blocker_type, created_at=q.created_at) for q in result.scalars().all()]


@router.post("/{team_id}/questions", response_model=TeamQuestionResponse, status_code=status.HTTP_201_CREATED)
async def add_question(team_id: str, data: TeamQuestionCreateRequest, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    require_starter(current_user, "Custom standup questions")
    max_idx = (await db.execute(select(func.max(TeamQuestion.order_index)).where(TeamQuestion.team_id == team.id))).scalar() or -1
    q = TeamQuestion(team_id=team.id, order_index=max_idx + 1, label=data.label, enabled=data.enabled, is_blocker_type=data.is_blocker_type)
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return TeamQuestionResponse(id=str(q.id), team_id=str(q.team_id), order_index=q.order_index, label=q.label, enabled=q.enabled, is_blocker_type=q.is_blocker_type, created_at=q.created_at)


@router.put("/{team_id}/questions/{question_id}", response_model=TeamQuestionResponse)
async def update_question(team_id: str, question_id: str, data: TeamQuestionUpdateRequest, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    result = await db.execute(select(TeamQuestion).where(and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)))
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    if data.label is not None:
        q.label = data.label
    if data.enabled is not None:
        q.enabled = data.enabled
    if data.is_blocker_type is not None:
        q.is_blocker_type = data.is_blocker_type
    if data.order_index is not None:
        q.order_index = data.order_index
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return TeamQuestionResponse(id=str(q.id), team_id=str(q.team_id), order_index=q.order_index, label=q.label, enabled=q.enabled, is_blocker_type=q.is_blocker_type, created_at=q.created_at)


@router.delete("/{team_id}/questions/{question_id}", status_code=status.HTTP_200_OK)
async def delete_question(team_id: str, question_id: str, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    enabled_count = (await db.execute(select(func.count(TeamQuestion.id)).where(and_(TeamQuestion.team_id == team.id, TeamQuestion.enabled == True)))).scalar() or 0
    result = await db.execute(select(TeamQuestion).where(and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)))
    q = result.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    if enabled_count <= 1 and q.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one question must remain enabled.")
    await db.delete(q)
    await db.commit()
    return {"message": "Question deleted"}


@router.post("/{team_id}/invite", status_code=status.HTTP_200_OK)
async def invite_members(team_id: str, data: InviteMembersRequest, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not user_has_starter_access(current_user):
        member_count = (await db.execute(select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id))).scalar() or 0
        pending_count = (await db.execute(select(func.count(Invite.id)).where(and_(Invite.team_id == team.id, Invite.used == False, Invite.expires_at > now)))).scalar() or 0
        total = member_count + pending_count
        if total + len(data.emails) > FREE_MEMBER_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"message": f"Free plan allows {FREE_MEMBER_LIMIT} invited member per team. You currently have {total}.", "upgrade_required": True, "current_count": total, "limit": FREE_MEMBER_LIMIT},
            )

    sent, failed, skipped = 0, [], []
    for email in data.emails:
        if email.lower() == current_user.email.lower():
            skipped.append({"email": email, "reason": "cannot invite yourself"})
            continue
        existing_invite = (await db.execute(select(Invite).where(and_(Invite.team_id == team.id, Invite.email == email, Invite.used == False, Invite.expires_at > now)))).scalar_one_or_none()
        if existing_invite:
            skipped.append({"email": email, "reason": "pending invite already exists"})
            continue
        existing_member = (await db.execute(select(TeamMember).join(User, TeamMember.user_id == User.id).where(and_(TeamMember.team_id == team.id, User.email == email, TeamMember.status == "active")))).scalar_one_or_none()
        if existing_member:
            skipped.append({"email": email, "reason": "already a team member"})
            continue
        token = str(uuid.uuid4())
        invite = Invite(team_id=team.id, email=email, token=token, expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7))
        db.add(invite)
        try:
            send_invite_email(to_email=email, team_name=team.name, invite_token=token)
            sent += 1
        except Exception as e:
            logger.error("teams.invite_email_failed", email=email, error=str(e))
            failed.append({"email": email, "error": str(e)})
            await db.delete(invite)

    await db.commit()
    if failed and sent == 0:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": "All invite emails failed.", "failures": failed})
    return {"message": f"Invites sent to {sent} member(s).", "sent": sent, "failed": failed, "skipped": skipped}


@router.get("/{team_id}/pending-invites", response_model=list[PendingInviteResponse])
async def list_pending_invites(team_id: str, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(select(Invite).where(and_(Invite.team_id == team.id, Invite.used == False, Invite.expires_at > now)).order_by(Invite.created_at))
    return [PendingInviteResponse(id=str(inv.id), email=inv.email, created_at=inv.created_at, expires_at=inv.expires_at) for inv in result.scalars().all()]


@router.delete("/{team_id}/pending-invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(team_id: str, invite_id: str, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    result = await db.execute(select(Invite).where(and_(Invite.id == invite_id, Invite.team_id == team.id, Invite.used == False)))
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    await db.delete(invite)
    await db.commit()


@router.get("/{team_id}/members", response_model=list[TeamMemberDetailResponse])
async def list_team_members(team_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_access(team_id, current_user, db)
    today = date.today()
    rows = (await db.execute(select(TeamMember, User).join(User, TeamMember.user_id == User.id).where(TeamMember.team_id == team.id).order_by(TeamMember.created_at))).all()
    result = []
    for tm, user in rows:
        checkin = (await db.execute(select(Checkin).where(and_(Checkin.team_id == team.id, Checkin.user_id == user.id, Checkin.date == today)))).scalars().first()
        result.append(TeamMemberDetailResponse(id=str(tm.id), user_id=str(user.id), team_id=str(team.id), name=user.name, email=user.email, role=tm.role, status=tm.status, checked_in_today=bool(checkin and checkin.submitted_at), submitted_at=checkin.submitted_at if checkin else None, created_at=tm.created_at, hourly_rate=tm.hourly_rate, timezone=tm.timezone, send_time=tm.send_time, currency=tm.currency, hours_per_day=tm.hours_per_day, hours_confirmed=tm.hours_confirmed or False))
    return result


@router.put("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def update_member(team_id: str, user_id: str, data: UpdateMemberRequest, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    result = await db.execute(select(TeamMember).where(and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if data.hourly_rate is not None:
        member.hourly_rate = data.hourly_rate
    if data.timezone is not None:
        member.timezone = data.timezone
    if data.send_time is not None:
        member.send_time = data.send_time
    if data.currency is not None:
        member.currency = data.currency
    if data.hours_per_day is not None:
        member.hours_per_day = data.hours_per_day
    if data.hours_confirmed is not None:
        member.hours_confirmed = data.hours_confirmed
    db.add(member)
    await db.commit()
    return {"message": "Member updated successfully"}


@router.delete("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def remove_member(team_id: str, user_id: str, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    result = await db.execute(select(TeamMember).where(and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)))
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    await db.delete(member)
    await db.commit()
    return {"message": "Member removed successfully"}


@router.post("/{team_id}/co-manager", status_code=status.HTTP_200_OK)
async def add_co_manager(team_id: str, data: dict, current_user: User = Depends(require_manager), db: AsyncSession = Depends(get_db)):
    team, _ = await require_team_manager(team_id, current_user, db)
    email = data.get("email")
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User {email} not found")
    existing = (await db.execute(select(TeamMember).where(and_(TeamMember.team_id == team.id, TeamMember.user_id == user.id)))).scalar_one_or_none()
    if existing:
        existing.role = "co-manager"
        if existing.status == "pending":
            existing.status = "active"
        db.add(existing)
    else:
        db.add(TeamMember(team_id=team.id, user_id=user.id, role="co-manager", status="active"))
    await db.commit()
    return {"message": f"{email} is now a co-manager of {team.name}"}
