import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from database import get_db
from models import Invite, Team, User, TeamMember
from schemas import InviteInfoResponse, AcceptInviteRequest, TokenResponse
from auth import hash_password, create_access_token
from datetime import datetime

router = APIRouter()


@router.get("/{token}", response_model=InviteInfoResponse)
async def get_invite(token: str, db: AsyncSession = Depends(get_db)):
    """Validate an invite token and return the invited email + team name."""
    result = await db.execute(select(Invite).where(Invite.token == token))
    invite = result.scalar_one_or_none()

    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite link is invalid")

    if invite.used:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has already been used")

    if datetime.utcnow() > invite.expires_at:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has expired")

    team_result = await db.execute(select(Team).where(Team.id == invite.team_id))
    team = team_result.scalar_one_or_none()

    return InviteInfoResponse(
        email=invite.email,
        team_name=team.name if team else "Unknown Team",
    )


@router.post("/{token}/accept", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def accept_invite(
    token: str,
    data: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Accept an invite: create account, join team, mark invite used."""
    result = await db.execute(select(Invite).where(Invite.token == token))
    invite = result.scalar_one_or_none()

    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite link is invalid")

    if invite.used:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has already been used")

    if datetime.utcnow() > invite.expires_at:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has expired")

    # Check if an account with this email already exists
    existing_user_result = await db.execute(select(User).where(User.email == invite.email))
    existing_user = existing_user_result.scalar_one_or_none()

    if existing_user:
        # If user already exists, just add them to the team if not already a member
        user = existing_user
    else:
        # Create new user account
        user = User(
            email=invite.email,
            name=data.name,
            password=hash_password(data.password),
            role="member",
        )
        db.add(user)
        await db.flush()  # get user.id before using it below

    # Check if already a team member
    existing_member_result = await db.execute(
        select(TeamMember).where(
            and_(TeamMember.team_id == invite.team_id, TeamMember.user_id == user.id)
        )
    )
    if not existing_member_result.scalar_one_or_none():
        team_member = TeamMember(
            team_id=invite.team_id,
            user_id=user.id,
            status="active",
        )
        db.add(team_member)

    # Mark invite as used
    invite.used = True

    await db.commit()
    await db.refresh(user)

    jwt_token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=jwt_token,
        user_id=str(user.id),
        name=user.name or "",
        role=user.role,
    )
