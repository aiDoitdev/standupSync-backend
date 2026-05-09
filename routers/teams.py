import uuid
import logging
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from database import get_db
from models import Team, TeamMember, TeamQuestion, User, Invite, Checkin, Blocker
from schemas import (
    CreateTeamRequest,
    TeamResponse,
    TeamDetailResponse,
    UserTeamsResponse,
    InviteMembersRequest,
    MemberStatusResponse,
    TeamMemberDetailResponse,
    TeamQuestionResponse,
    TeamQuestionCreateRequest,
    TeamQuestionUpdateRequest,
    UpdateMemberRequest,
    PendingInviteResponse,
    ConfirmHoursRequest,
)
from auth import (
    get_current_user,
    require_manager,
    require_team_access,
    require_team_manager,
)
from email_service import send_invite_email
from plan_limits import FREE_MEMBER_LIMIT, team_has_starter_access, require_starter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/create", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: CreateTeamRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Create a new team. Free plan: 1 team. Starter plan: unlimited teams."""
    # Check if user already has a team on the free plan
    existing_teams = await db.execute(
        select(Team).where(Team.manager_id == current_user.id)
    )
    user_teams = existing_teams.scalars().all()

    if user_teams:
        has_starter = team_has_starter_access(current_user)
        if not has_starter:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Free plan allows only 1 team. Upgrade any team to Starter ($19/mo) for unlimited teams.",
            )

    team = Team(
        name=data.name,
        manager_id=current_user.id,
        team_type=data.team_type,
    )
    db.add(team)
    await db.flush()  # get team.id before creating questions

    # Create 3 default standup questions for every new team
    default_questions = [
        TeamQuestion(team_id=team.id, order_index=0, label="What did you accomplish yesterday?", enabled=True, is_blocker_type=False),
        TeamQuestion(team_id=team.id, order_index=1, label="What will you work on today?", enabled=True, is_blocker_type=False),
        TeamQuestion(team_id=team.id, order_index=2, label="Any blockers or issues?", enabled=True, is_blocker_type=True),
    ]
    for q in default_questions:
        db.add(q)

    await db.commit()
    await db.refresh(team)

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=0,
    )


@router.get("/", response_model=list[UserTeamsResponse])
async def get_all_teams(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all teams where user is manager or active member."""
    teams_list = []

    # Get teams user manages
    if current_user.role == "manager":
        result = await db.execute(select(Team).where(Team.manager_id == current_user.id))
        managed_teams = result.scalars().all()

        for team in managed_teams:
            count_result = await db.execute(
                select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
            )
            member_count = count_result.scalar() or 0

            teams_list.append(
                UserTeamsResponse(
                    id=str(team.id),
                    name=team.name,
                    user_role="owner",
                    member_count=member_count,
                    plan=current_user.plan or "free",
                    plan_status=current_user.plan_status or "active",
                    created_at=team.created_at,
                    team_type=team.team_type,
                )
            )

    # Get teams user is a member of
    result = await db.execute(
        select(TeamMember, Team)
        .join(Team, TeamMember.team_id == Team.id)
        .where(
            and_(
                TeamMember.user_id == current_user.id,
                TeamMember.status == "active",
            )
        )
    )
    member_teams = result.all()

    for tm, team in member_teams:
        count_result = await db.execute(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
        )
        member_count = count_result.scalar() or 0

        mgr_result = await db.execute(select(User).where(User.id == team.manager_id))
        mgr = mgr_result.scalar_one_or_none()

        teams_list.append(
            UserTeamsResponse(
                id=str(team.id),
                name=team.name,
                user_role="member",
                member_count=member_count,
                plan=mgr.plan if mgr else "free",
                plan_status=mgr.plan_status if mgr else "active",
                created_at=team.created_at,
                team_type=team.team_type,
            )
        )

    return teams_list


# ---------------------------------------------------------------------------
# Legacy endpoints (kept BEFORE /{team_id} to prevent route shadowing)
# ---------------------------------------------------------------------------

# Legacy endpoint for backward compatibility
@router.get("/my", response_model=TeamResponse, deprecated=True)
async def get_my_team(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Get the first team for manager. Use GET / instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
    )


# Legacy endpoint for backward compatibility
@router.get("/members", response_model=list[MemberStatusResponse], deprecated=True)
async def list_members_legacy(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: List members of first team. Use GET /{team_id}/members instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    today = date.today()
    rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
    )
    members = rows.all()

    response = []
    for tm, user in members:
        checkin_result = await db.execute(
            select(Checkin).where(
                and_(
                    Checkin.team_id == team.id,
                    Checkin.user_id == user.id,
                    Checkin.date == today,
                )
            )
        )
        checkin = checkin_result.scalars().first()

        response.append(
            MemberStatusResponse(
                user_id=str(user.id),
                name=user.name,
                email=user.email,
                member_status=tm.status,
                checked_in_today=bool(checkin and checkin.submitted_at),
                submitted_at=checkin.submitted_at if checkin else None,
            )
        )

    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ensure_team_questions(team: Team, db: AsyncSession) -> None:
    """Lazily migrate legacy q1/q2/q3 labels to TeamQuestion rows if none exist yet."""
    try:
        existing = await db.execute(
            select(TeamQuestion).where(TeamQuestion.team_id == team.id).limit(1)
        )
        if existing.scalar_one_or_none():
            return  # questions already exist

        seeds = [
            TeamQuestion(team_id=team.id, order_index=0, label=team.q1_label or "What did you accomplish yesterday?", enabled=True, is_blocker_type=False),
            TeamQuestion(team_id=team.id, order_index=1, label=team.q2_label or "What will you work on today?", enabled=True, is_blocker_type=False),
            TeamQuestion(team_id=team.id, order_index=2, label=team.q3_label or "Any blockers or issues?", enabled=True, is_blocker_type=True),
        ]
        for q in seeds:
            db.add(q)
        await db.commit()
    except Exception:
        logger.exception("Failed to seed default questions for team %s — continuing without seeding", team.id)
        await db.rollback()


# ---------------------------------------------------------------------------
# Team CRUD (parameterized routes — must be after static legacy routes above)
# ---------------------------------------------------------------------------

@router.get("/{team_id}", response_model=TeamDetailResponse)
async def get_team_details(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get team details. User must be manager or active member."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Lazy-migrate: if this team has no TeamQuestion rows yet, seed from legacy q*_label columns
    await _ensure_team_questions(team, db)

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    mgr_result = await db.execute(select(User).where(User.id == team.manager_id))
    mgr = mgr_result.scalar_one_or_none()

    return TeamDetailResponse(
        id=str(team.id),
        name=team.name,
        plan=mgr.plan if mgr else "free",
        member_count=member_count,
        created_at=team.created_at,
        team_type=team.team_type,
    )


@router.put("/{team_id}", response_model=TeamDetailResponse)
async def update_team(
    team_id: str,
    data: CreateTeamRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update team name and type. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    team.name = data.name
    team.team_type = data.team_type
    db.add(team)
    await db.commit()
    await db.refresh(team)

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamDetailResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
        created_at=team.created_at,
        team_type=team.team_type,
    )


# ---------------------------------------------------------------------------
# Team Questions CRUD (Issue 4: configurable standup questions)
# ---------------------------------------------------------------------------

@router.get("/{team_id}/questions", response_model=list[TeamQuestionResponse])
async def list_questions(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all questions for a team, ordered by order_index."""
    team, _ = await require_team_access(team_id, current_user, db)
    await _ensure_team_questions(team, db)

    result = await db.execute(
        select(TeamQuestion)
        .where(TeamQuestion.team_id == team.id)
        .order_by(TeamQuestion.order_index)
    )
    return [
        TeamQuestionResponse(
            id=str(q.id), team_id=str(q.team_id), order_index=q.order_index,
            label=q.label, enabled=q.enabled, is_blocker_type=q.is_blocker_type,
            created_at=q.created_at,
        )
        for q in result.scalars().all()
    ]


@router.post("/{team_id}/questions", response_model=TeamQuestionResponse, status_code=status.HTTP_201_CREATED)
async def add_question(
    team_id: str,
    data: TeamQuestionCreateRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Add a new question to the team. Manager only. Requires Starter plan."""
    team, _ = await require_team_manager(team_id, current_user, db)
    require_starter(current_user, "Custom standup questions")

    # Determine next order_index
    max_result = await db.execute(
        select(func.max(TeamQuestion.order_index)).where(TeamQuestion.team_id == team.id)
    )
    max_idx = max_result.scalar() or -1

    question = TeamQuestion(
        team_id=team.id,
        order_index=max_idx + 1,
        label=data.label,
        enabled=data.enabled,
        is_blocker_type=data.is_blocker_type,
    )
    db.add(question)
    await db.commit()
    await db.refresh(question)

    return TeamQuestionResponse(
        id=str(question.id), team_id=str(question.team_id), order_index=question.order_index,
        label=question.label, enabled=question.enabled, is_blocker_type=question.is_blocker_type,
        created_at=question.created_at,
    )


@router.put("/{team_id}/questions/{question_id}", response_model=TeamQuestionResponse)
async def update_question(
    team_id: str,
    question_id: str,
    data: TeamQuestionUpdateRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update a question's label, enabled status, blocker flag, or order. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamQuestion).where(
            and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    if data.label is not None:
        question.label = data.label
    if data.enabled is not None:
        question.enabled = data.enabled
    if data.is_blocker_type is not None:
        question.is_blocker_type = data.is_blocker_type
    if data.order_index is not None:
        question.order_index = data.order_index

    db.add(question)
    await db.commit()
    await db.refresh(question)

    return TeamQuestionResponse(
        id=str(question.id), team_id=str(question.team_id), order_index=question.order_index,
        label=question.label, enabled=question.enabled, is_blocker_type=question.is_blocker_type,
        created_at=question.created_at,
    )


@router.delete("/{team_id}/questions/{question_id}", status_code=status.HTTP_200_OK)
async def delete_question(
    team_id: str,
    question_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Delete a question. At least 1 question must remain. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    count_result = await db.execute(
        select(func.count(TeamQuestion.id)).where(
            and_(TeamQuestion.team_id == team.id, TeamQuestion.enabled == True)
        )
    )
    enabled_count = count_result.scalar() or 0

    result = await db.execute(
        select(TeamQuestion).where(
            and_(TeamQuestion.id == question_id, TeamQuestion.team_id == team.id)
        )
    )
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    if enabled_count <= 1 and question.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one question must remain enabled.",
        )

    await db.delete(question)
    await db.commit()
    return {"message": "Question deleted"}


@router.post("/{team_id}/invite", status_code=status.HTTP_200_OK)
async def invite_members(
    team_id: str,
    data: InviteMembersRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Invite members to a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    # Enforce free-plan member limit (active members + pending invites)
    if not team_has_starter_access(current_user):
        count_result = await db.execute(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
        )
        member_count = count_result.scalar() or 0
        pending_result = await db.execute(
            select(func.count(Invite.id)).where(
                and_(Invite.team_id == team.id, Invite.used == False)
            )
        )
        pending_count = pending_result.scalar() or 0
        total = member_count + pending_count
        if total + len(data.emails) > FREE_MEMBER_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "message": (
                        f"Free plan allows {FREE_MEMBER_LIMIT} invited member per team. "
                        f"You currently have {total}. "
                        "Upgrade to Starter ($19/mo) for unlimited members and teams."
                    ),
                    "upgrade_required": True,
                    "current_count": total,
                    "limit": FREE_MEMBER_LIMIT,
                },
            )

    sent = 0
    failed = []
    skipped = []

    for email in data.emails:
        # Skip if a pending invite already exists
        existing_invite = await db.execute(
            select(Invite).where(
                and_(
                    Invite.team_id == team.id,
                    Invite.email == email,
                    Invite.used == False,
                )
            )
        )
        if existing_invite.scalar_one_or_none():
            skipped.append({"email": email, "reason": "pending invite already exists"})
            continue

        # Skip if the email already belongs to an active team member
        existing_member = await db.execute(
            select(TeamMember)
            .join(User, TeamMember.user_id == User.id)
            .where(
                and_(
                    TeamMember.team_id == team.id,
                    User.email == email,
                    TeamMember.status == "active",
                )
            )
        )
        if existing_member.scalar_one_or_none():
            skipped.append({"email": email, "reason": "already a team member"})
            continue

        token = str(uuid.uuid4())
        invite = Invite(team_id=team.id, email=email, token=token)
        db.add(invite)
        try:
            send_invite_email(to_email=email, team_name=team.name, invite_token=token)
            sent += 1
        except Exception as e:
            logger.error("Failed to send invite to %s: %s", email, e)
            failed.append({"email": email, "error": str(e)})
            await db.delete(invite)

    await db.commit()

    if failed and sent == 0:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "All invite emails failed to send. Check your Resend domain verification.",
                "failures": failed,
            },
        )

    return {
        "message": f"Invites sent to {sent} member(s).",
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
    }


@router.get("/{team_id}/pending-invites", response_model=list[PendingInviteResponse])
async def list_pending_invites(
    team_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """List pending (unused, non-expired) invites for a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    now = datetime.utcnow()
    result = await db.execute(
        select(Invite).where(
            and_(
                Invite.team_id == team.id,
                Invite.used == False,
                Invite.expires_at > now,
            )
        ).order_by(Invite.created_at)
    )
    invites = result.scalars().all()
    return [
        PendingInviteResponse(
            id=str(inv.id),
            email=inv.email,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in invites
    ]


@router.delete("/{team_id}/pending-invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    team_id: str,
    invite_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Revoke (delete) a pending invite. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(Invite).where(
            and_(
                Invite.id == invite_id,
                Invite.team_id == team.id,
                Invite.used == False,
            )
        )
    )
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    await db.delete(invite)
    await db.commit()


@router.get("/{team_id}/members", response_model=list[TeamMemberDetailResponse])
async def list_team_members(
    team_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a team. User must be manager or member."""
    team, _ = await require_team_access(team_id, current_user, db)
    today = date.today()

    rows = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
        .order_by(TeamMember.created_at)
    )
    members = rows.all()

    result = []
    for tm, user in members:
        # Check today's checkin status
        checkin_result = await db.execute(
            select(Checkin).where(
                and_(
                    Checkin.team_id == team.id,
                    Checkin.user_id == user.id,
                    Checkin.date == today,
                )
            )
        )
        checkin = checkin_result.scalars().first()

        result.append(
            TeamMemberDetailResponse(
                id=str(tm.id),
                user_id=str(user.id),
                team_id=str(team.id),
                name=user.name,
                email=user.email,
                role=tm.role,
                status=tm.status,
                checked_in_today=bool(checkin and checkin.submitted_at),
                submitted_at=checkin.submitted_at if checkin else None,
                created_at=tm.created_at,
                hourly_rate=tm.hourly_rate,
                timezone=tm.timezone or "Asia/Kolkata",
                send_time=tm.send_time or "09:00",
                currency=tm.currency or "INR",
                hours_per_day=tm.hours_per_day,
                hours_confirmed=tm.hours_confirmed or False,
            )
        )

    return result


@router.put("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def update_member(
    team_id: str,
    user_id: str,
    data: UpdateMemberRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Update member settings (e.g. hourly_rate). Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    if data.hourly_rate is not None:
        member.hourly_rate = data.hourly_rate
    elif "hourly_rate" in data.model_fields_set:
        member.hourly_rate = None
    if data.timezone is not None:
        member.timezone = data.timezone
    if data.send_time is not None:
        member.send_time = data.send_time
    if data.currency is not None:
        member.currency = data.currency
    if data.hours_per_day is not None:
        member.hours_per_day = data.hours_per_day
    elif "hours_per_day" in data.model_fields_set:
        member.hours_per_day = None
    if data.hours_confirmed is not None:
        member.hours_confirmed = data.hours_confirmed
    db.add(member)
    await db.commit()

    return {"message": "Member updated successfully"}


@router.delete("/{team_id}/member/{user_id}", status_code=status.HTTP_200_OK)
async def remove_member(
    team_id: str,
    user_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Remove a member from a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )

    await db.delete(member)
    await db.commit()

    return {"message": "Member removed successfully"}


@router.post("/{team_id}/co-manager", status_code=status.HTTP_200_OK)
async def add_co_manager(
    team_id: str,
    data: dict,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """Add a co-manager to a team. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    email = data.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required",
        )

    # Find user with this email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with email {email} not found",
        )

    # Check if already a member
    result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == team.id, TeamMember.user_id == user.id)
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.role = "co-manager"
        if existing.status == "pending":
            existing.status = "active"
        db.add(existing)
    else:
        member = TeamMember(
            team_id=team.id,
            user_id=user.id,
            role="co-manager",
            status="active",
        )
        db.add(member)

    await db.commit()

    return {"message": f"{email} is now a co-manager of {team.name}"}


# Legacy endpoint for backward compatibility (GET /my and GET /members moved to top, before /{team_id})
@router.post("/invite", status_code=status.HTTP_200_OK, deprecated=True)
async def invite_members_legacy_post(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Get the first team for manager. Use GET / instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    count_result = await db.execute(
        select(func.count(TeamMember.id)).where(TeamMember.team_id == team.id)
    )
    member_count = count_result.scalar() or 0

    return TeamResponse(
        id=str(team.id),
        name=team.name,
        plan=current_user.plan,
        member_count=member_count,
    )


async def invite_members_legacy_post(
    data: InviteMembersRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    """DEPRECATED: Invite members to first team. Use POST /{team_id}/invite instead."""
    result = await db.execute(
        select(Team).where(Team.manager_id == current_user.id).limit(1)
    )
    team = result.scalar_one_or_none()

    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No team found. Create a team first.",
        )

    return await invite_members(str(team.id), data, current_user, db)
