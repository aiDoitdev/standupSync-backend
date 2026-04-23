from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class SendOTPRequest(BaseModel):
    email: EmailStr
    name: str
    password: str

    @field_validator("password")
    @classmethod
    def _min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class VerifySignupRequest(BaseModel):
    email: EmailStr
    name: str
    password: str
    otp_code: str

    @field_validator("password")
    @classmethod
    def _min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    name: str
    role: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    role: str
    created_at: datetime
