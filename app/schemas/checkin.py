from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


class QuestionItem(BaseModel):
    id: str
    label: str
    is_blocker_type: bool
    required: bool = True


class CheckinTokenResponse(BaseModel):
    member_name: str
    team_name: str
    date: str
    already_submitted: bool
    questions: List[QuestionItem] = []
    hours_confirmation_needed: bool = False
    hours_per_day: Optional[float] = None


class AnswerItem(BaseModel):
    question_id: str
    answer: str


class SubmitCheckinRequest(BaseModel):
    answers: List[AnswerItem]


class ConfirmHoursRequest(BaseModel):
    hours_per_day: float

    @field_validator("hours_per_day")
    @classmethod
    def _validate_hours(cls, v: float) -> float:
        if v < 0.5 or v > 24:
            raise ValueError("hours_per_day must be between 0.5 and 24")
        return v


class CheckinAnswerResponse(BaseModel):
    question_id: str
    question_label: str
    answer: str
    is_blocker_type: bool


class CheckinResponse(BaseModel):
    id: str
    user_id: str
    member_name: Optional[str]
    date: str
    yesterday: Optional[str]
    today: Optional[str]
    blockers: Optional[str]
    answers: List[CheckinAnswerResponse] = []
    submitted_at: Optional[datetime]


class CheckinHistoryItem(BaseModel):
    date: str
    yesterday: Optional[str]
    today: Optional[str]
    blockers: Optional[str]
    answers: List[CheckinAnswerResponse] = []
    submitted_at: Optional[datetime]
