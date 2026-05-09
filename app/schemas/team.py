from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from app.core.types import LenientEmailStr


class CreateTeamRequest(BaseModel):
    name: str
    team_type: Optional[str] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    plan: str
    member_count: int


class TeamDetailResponse(BaseModel):
    id: str
    name: str
    plan: str
    member_count: int
    created_at: datetime
    team_type: Optional[str] = None


class UserTeamsResponse(BaseModel):
    id: str
    name: str
    user_role: str
    member_count: int
    plan: str = "free"
    plan_status: str = "active"
    created_at: datetime
    team_type: Optional[str] = None


class InviteMembersRequest(BaseModel):
    emails: List[LenientEmailStr]


class MemberStatusResponse(BaseModel):
    user_id: str
    name: Optional[str]
    email: str
    member_status: str
    checked_in_today: bool
    submitted_at: Optional[datetime]


class TeamMemberDetailResponse(BaseModel):
    id: str
    user_id: str
    team_id: str
    name: Optional[str]
    email: str
    role: str
    status: str
    checked_in_today: bool
    submitted_at: Optional[datetime]
    created_at: datetime
    hourly_rate: Optional[float] = None
    timezone: Optional[str] = "Asia/Kolkata"
    send_time: Optional[str] = "09:00"
    currency: Optional[str] = "INR"
    hours_per_day: Optional[float] = None
    hours_confirmed: bool = False


class UpdateMemberRequest(BaseModel):
    hourly_rate: Optional[float] = None
    timezone: Optional[str] = None
    send_time: Optional[str] = None
    currency: Optional[str] = None
    hours_per_day: Optional[float] = None
    hours_confirmed: Optional[bool] = None


class PendingInviteResponse(BaseModel):
    id: str
    email: str
    created_at: datetime
    expires_at: datetime


class InviteInfoResponse(BaseModel):
    email: str
    team_name: str


class AcceptInviteRequest(BaseModel):
    name: str
    password: str


class TeamQuestionResponse(BaseModel):
    id: str
    team_id: str
    order_index: int
    label: str
    enabled: bool
    is_blocker_type: bool
    created_at: datetime


class TeamQuestionCreateRequest(BaseModel):
    label: str
    enabled: bool = True
    is_blocker_type: bool = False


class TeamQuestionUpdateRequest(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    is_blocker_type: Optional[bool] = None
    order_index: Optional[int] = None
