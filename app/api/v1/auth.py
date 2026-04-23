import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.security import hash_password, verify_password, create_access_token
from app.models.user import User
from app.models.checkin import OTPVerification
from app.schemas.auth import (
    SendOTPRequest, VerifySignupRequest, LoginRequest, TokenResponse, UserResponse,
)
from app.services.email_service import send_otp_email
from app.utils.rate_limiter import limiter

router = APIRouter()

OTP_EXPIRY_MINUTES = 10


@router.post("/signup/send-otp", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def send_signup_otp(
    request: Request,
    data: SendOTPRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    await db.execute(
        update(OTPVerification)
        .where(OTPVerification.email == data.email, OTPVerification.used == False)
        .values(used=True)
    )

    otp_code = f"{secrets.randbelow(1_000_000):06d}"
    otp_record = OTPVerification(
        email=data.email,
        otp_code=otp_code,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.add(otp_record)
    await db.commit()

    send_otp_email(data.email, otp_code)
    return {"message": "Verification code sent"}


@router.post("/signup/verify-otp", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def verify_signup_otp(
    request: Request,
    data: VerifySignupRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    result = await db.execute(
        select(OTPVerification)
        .where(OTPVerification.email == data.email, OTPVerification.used == False)
        .order_by(OTPVerification.created_at.desc())
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No verification code found. Please request a new one.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if now > otp_record.expires_at:
        otp_record.used = True
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired. Please request a new one.")

    if otp_record.otp_code != data.otp_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code.")

    otp_record.used = True
    user = User(email=data.email, name=data.name, password=hash_password(data.password), role="manager")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user_id=str(user.id),
        name=user.name or "",
        role=user.role,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return TokenResponse(
        access_token=create_access_token(str(user.id)),
        user_id=str(user.id),
        name=user.name or "",
        role=user.role,
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        role=current_user.role,
        created_at=current_user.created_at,
    )
