from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.database import get_db
from app.core.security import hash_password, create_access_token
from app.models.team import Team, TeamMember, Invite
from app.models.user import User
from app.schemas.team import InviteInfoResponse, AcceptInviteRequest
from app.schemas.auth import TokenResponse

router = APIRouter()


@router.get("/{token}", response_model=InviteInfoResponse)
async def get_invite(token: str, db: AsyncSession = Depends(get_db)):
    invite = (await db.execute(select(Invite).where(Invite.token == token))).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite link is invalid")
    if invite.used:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has already been used")
    if datetime.now(timezone.utc).replace(tzinfo=None) > invite.expires_at:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has expired")
    team = (await db.execute(select(Team).where(Team.id == invite.team_id))).scalar_one_or_none()
    return InviteInfoResponse(email=invite.email, team_name=team.name if team else "Unknown Team")


@router.post("/{token}/accept", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def accept_invite(token: str, data: AcceptInviteRequest, db: AsyncSession = Depends(get_db)):
    invite = (await db.execute(select(Invite).where(Invite.token == token))).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite link is invalid")
    if invite.used:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has already been used")
    if datetime.now(timezone.utc).replace(tzinfo=None) > invite.expires_at:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite link has expired")

    user = (await db.execute(select(User).where(User.email == invite.email))).scalar_one_or_none()
    if not user:
        user = User(email=invite.email, name=data.name, password=hash_password(data.password), role="member")
        db.add(user)
        await db.flush()

    existing_member = (await db.execute(select(TeamMember).where(and_(TeamMember.team_id == invite.team_id, TeamMember.user_id == user.id)))).scalar_one_or_none()
    if not existing_member:
        db.add(TeamMember(team_id=invite.team_id, user_id=user.id, status="active"))

    invite.used = True
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user_id=str(user.id),
        name=user.name or "",
        role=user.role,
    )
