import uuid
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from database import get_db
from models import User, OTPVerification
from schemas import SignupRequest, LoginRequest, TokenResponse, UserResponse, SendOTPRequest, VerifySignupRequest
from auth import hash_password, verify_password, create_access_token, get_current_user
from email_service import send_otp_email

router = APIRouter()

OTP_EXPIRY_MINUTES = 10


@router.post("/signup/send-otp", status_code=status.HTTP_200_OK)
async def send_signup_otp(data: SendOTPRequest, db: AsyncSession = Depends(get_db)):
    """Step 1: validate details and send a 6-digit OTP to the given email."""
    if len(data.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )

    # Reject already-registered emails
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # Invalidate any previous unused OTPs for this email
    await db.execute(
        update(OTPVerification)
        .where(OTPVerification.email == data.email, OTPVerification.used == False)
        .values(used=True)
    )

    # Generate a cryptographically secure 6-digit OTP
    otp_code = f"{secrets.randbelow(1_000_000):06d}"

    otp_record = OTPVerification(
        email=data.email,
        otp_code=otp_code,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.add(otp_record)
    await db.commit()

    send_otp_email(data.email, otp_code)
    return {"message": "Verification code sent"}


@router.post("/signup/verify-otp", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def verify_signup_otp(data: VerifySignupRequest, db: AsyncSession = Depends(get_db)):
    """Step 2: verify OTP and create the user account."""
    if len(data.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )

    # Guard against race conditions where email was registered between steps
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # Find the most recent unused OTP for this email
    result = await db.execute(
        select(OTPVerification)
        .where(OTPVerification.email == data.email, OTPVerification.used == False)
        .order_by(OTPVerification.created_at.desc())
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code found. Please request a new one.",
        )

    if datetime.utcnow() > otp_record.expires_at:
        otp_record.used = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new one.",
        )

    if otp_record.otp_code != data.otp_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code.",
        )

    # Mark OTP consumed before creating the user
    otp_record.used = True

    user = User(
        email=data.email,
        name=data.name,
        password=hash_password(data.password),
        role="manager",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        user_id=str(user.id),
        name=user.name or "",
        role=user.role,
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
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
