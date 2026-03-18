from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from .models import (
    ApprovalDecisionEnum,
    RecurrenceTypeEnum,
    RedemptionStatusEnum,
    RoleEnum,
    TaskStatusEnum,
)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    login: str | None = Field(default=None, min_length=2, max_length=255)
    email: EmailStr | None = None
    password: str


class BootstrapRequest(BaseModel):
    family_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class BootstrapStatusOut(BaseModel):
    bootstrap_required: bool


class UserOut(BaseModel):
    id: int
    email: EmailStr
    display_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class FamilyOut(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class FamilyMemberOut(BaseModel):
    membership_id: int
    family_id: int
    user_id: int
    display_name: str
    email: EmailStr
    is_active: bool
    role: RoleEnum
    created_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: RoleEnum


class MemberUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    role: RoleEnum
    is_active: bool = True
    password: str | None = Field(default=None, min_length=8, max_length=128)


class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    assignee_id: int
    due_at: datetime | None = None
    points: int = Field(default=0, ge=0)
    recurrence_type: RecurrenceTypeEnum = RecurrenceTypeEnum.none


class TaskUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    assignee_id: int
    due_at: datetime | None = None
    points: int = Field(default=0, ge=0)
    recurrence_type: RecurrenceTypeEnum = RecurrenceTypeEnum.none
    status: TaskStatusEnum = TaskStatusEnum.open


class TaskOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    assignee_id: int
    due_at: datetime | None
    points: int
    recurrence_type: RecurrenceTypeEnum
    status: TaskStatusEnum
    created_by_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskSubmitRequest(BaseModel):
    note: str | None = None


class TaskReviewRequest(BaseModel):
    decision: ApprovalDecisionEnum
    comment: str | None = None


class CalendarEventCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    responsible_user_id: int | None = None
    start_at: datetime
    end_at: datetime


class CalendarEventOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    responsible_user_id: int | None
    start_at: datetime
    end_at: datetime
    created_by_id: int

    model_config = {"from_attributes": True}


class RewardCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    cost_points: int = Field(ge=1)
    is_active: bool = True


class RewardUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    cost_points: int = Field(ge=1)
    is_active: bool = True


class RewardOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    cost_points: int
    is_active: bool

    model_config = {"from_attributes": True}


class RedemptionRequest(BaseModel):
    comment: str | None = None


class RedemptionReviewRequest(BaseModel):
    decision: RedemptionStatusEnum
    comment: str | None = None


class RedemptionOut(BaseModel):
    id: int
    reward_id: int
    requested_by_id: int
    status: RedemptionStatusEnum
    comment: str | None
    reviewed_by_id: int | None
    requested_at: datetime
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class LedgerEntryOut(BaseModel):
    id: int
    family_id: int
    user_id: int
    source_type: str
    source_id: int
    points_delta: int
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BalanceOut(BaseModel):
    family_id: int
    user_id: int
    balance: int


class BalanceItemOut(BaseModel):
    family_id: int
    user_id: int
    display_name: str
    role: RoleEnum
    balance: int


class PointsAdjustRequest(BaseModel):
    user_id: int
    points_delta: int = Field(ge=-9999, le=9999)
    description: str = Field(min_length=2, max_length=255)
