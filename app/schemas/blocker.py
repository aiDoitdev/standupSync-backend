from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class BlockerCommentResponse(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str]
    comment: str
    created_at: datetime


class BlockerResolutionResponse(BaseModel):
    id: str
    manager_id: str
    manager_name: Optional[str]
    unblock_instructions: str
    created_at: datetime


class BlockerDetailResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    user_name: Optional[str]
    user_email: Optional[str]
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    status: str
    title: str
    description: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    resolved_at: Optional[datetime]
    comments: List[BlockerCommentResponse] = []
    resolution: Optional[BlockerResolutionResponse] = None


class BlockerListItemResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    user_name: Optional[str]
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    status: str
    title: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    comment_count: int
    resolved_at: Optional[datetime]


class AssignBlockerRequest(BaseModel):
    assigned_to: Optional[str] = None


class AddBlockerCommentRequest(BaseModel):
    comment: str


class UpdateBlockerStatusRequest(BaseModel):
    status: str


class ResolveBlockerRequest(BaseModel):
    unblock_instructions: str
