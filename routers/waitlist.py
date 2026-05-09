from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from database import get_db
from models import WaitlistEntry
from pydantic import BaseModel
from schemas import LenientEmailStr

router = APIRouter()


class WaitlistJoinRequest(BaseModel):
    email: LenientEmailStr


class WaitlistJoinResponse(BaseModel):
    message: str
    count: int


class WaitlistCountResponse(BaseModel):
    count: int


@router.post("/join", response_model=WaitlistJoinResponse, status_code=status.HTTP_201_CREATED)
async def join_waitlist(data: WaitlistJoinRequest, db: AsyncSession = Depends(get_db)):
    """Add an email to the waitlist. Idempotent — duplicate emails are silently accepted."""
    result = await db.execute(select(WaitlistEntry).where(WaitlistEntry.email == data.email))
    existing = result.scalar_one_or_none()

    if not existing:
        entry = WaitlistEntry(email=data.email)
        db.add(entry)
        await db.commit()

    count_result = await db.execute(select(func.count()).select_from(WaitlistEntry))
    count = count_result.scalar()

    return WaitlistJoinResponse(message="You're on the waitlist!", count=count)


@router.get("/count", response_model=WaitlistCountResponse)
async def get_waitlist_count(db: AsyncSession = Depends(get_db)):
    """Return the total number of waitlist subscribers."""
    result = await db.execute(select(func.count()).select_from(WaitlistEntry))
    count = result.scalar()
    return WaitlistCountResponse(count=count)
