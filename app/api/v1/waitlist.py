from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, EmailStr

from app.core.database import get_db
from app.models.team import WaitlistEntry

router = APIRouter()


class WaitlistJoinRequest(BaseModel):
    email: EmailStr


class WaitlistJoinResponse(BaseModel):
    message: str
    count: int


class WaitlistCountResponse(BaseModel):
    count: int


@router.post("/join", response_model=WaitlistJoinResponse, status_code=201)
async def join_waitlist(data: WaitlistJoinRequest, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(WaitlistEntry).where(WaitlistEntry.email == data.email))).scalar_one_or_none()
    if not existing:
        db.add(WaitlistEntry(email=data.email))
        await db.commit()
    count = (await db.execute(select(func.count()).select_from(WaitlistEntry))).scalar()
    return WaitlistJoinResponse(message="You're on the waitlist!", count=count)


@router.get("/count", response_model=WaitlistCountResponse)
async def get_waitlist_count(db: AsyncSession = Depends(get_db)):
    count = (await db.execute(select(func.count()).select_from(WaitlistEntry))).scalar()
    return WaitlistCountResponse(count=count)
