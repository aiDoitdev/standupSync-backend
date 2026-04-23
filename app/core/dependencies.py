import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import decode_access_token

_security = HTTPBearer()

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
    db: AsyncSession = Depends(get_db),
):
    from app.models.user import User

    user_id = decode_access_token(credentials.credentials)
    if not user_id:
        raise _CREDENTIALS_EXCEPTION

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _CREDENTIALS_EXCEPTION
    return user


async def require_manager(current_user=Depends(get_current_user)):
    if current_user.role != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can perform this action",
        )
    return current_user


async def require_team_access(
    team_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.team import Team, TeamMember

    try:
        team_uuid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team ID format")

    result = await db.execute(select(Team).where(Team.id == team_uuid))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if team.manager_id == current_user.id:
        return team, current_user

    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_uuid,
            TeamMember.user_id == current_user.id,
            TeamMember.status == "active",
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this team",
        )
    return team, current_user


async def require_team_manager(
    team_id: str,
    current_user=Depends(require_manager),
    db: AsyncSession = Depends(get_db),
):
    from app.models.team import Team

    try:
        team_uuid = uuid.UUID(team_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid team ID format")

    result = await db.execute(select(Team).where(Team.id == team_uuid))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if team.manager_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the manager of this team",
        )
    return team, current_user
