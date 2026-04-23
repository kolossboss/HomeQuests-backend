from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from .models import (
    AchievementDifficultyEnum,
    AchievementFreezeScopeEnum,
    AchievementProgressStatusEnum,
    AchievementRewardKindEnum,
    AchievementRuleKindEnum,
    ApprovalDecisionEnum,
    NotificationChannelEnum,
    RecurrenceTypeEnum,
    RewardContributionStatusEnum,
    RedemptionStatusEnum,
    RoleEnum,
    SpecialTaskIntervalEnum,
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
    email: EmailStr | None = None
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=3, max_length=128)
    password_confirm: str = Field(min_length=3, max_length=128)

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.password != self.password_confirm:
            raise ValueError("Passwort und Passwort-Wiederholung stimmen nicht überein")
        return self


class BootstrapStatusOut(BaseModel):
    bootstrap_required: bool


class BootstrapBackupFileOut(BaseModel):
    file_name: str
    file_path: str
    size_bytes: int
    modified_at_utc: datetime


class BootstrapBackupListOut(BaseModel):
    backup_supported: bool
    restore_command_available: bool
    backup_allowed_dirs: list[str]
    upload_max_bytes: int
    files: list[BootstrapBackupFileOut]


class BootstrapRestoreRequest(BaseModel):
    backup_file: str = Field(min_length=1, max_length=1024)

    @field_validator("backup_file")
    @classmethod
    def normalize_backup_file(cls, value: str) -> str:
        return value.strip()


class BootstrapRestoreOut(BaseModel):
    restored: bool
    backup_file_path: str
    duration_seconds: float
    restored_at_utc: datetime
    database_engine: str
    user_count: int


class BootstrapBackupUploadOut(BaseModel):
    uploaded: bool
    file_name: str
    file_path: str
    size_bytes: int
    uploaded_at_utc: datetime


class UserOut(BaseModel):
    id: int
    email: EmailStr | None
    display_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class PushDeviceRegisterRequest(BaseModel):
    device_token: str = Field(min_length=32, max_length=255)
    bundle_id: str = Field(min_length=3, max_length=255)
    push_environment: Literal["development", "production"] = "production"
    notifications_enabled: bool = True
    child_new_task: bool = True
    manager_task_submitted: bool = True
    manager_reward_requested: bool = True
    task_due_reminder: bool = True

    @field_validator("device_token", "bundle_id")
    @classmethod
    def normalize_push_strings(cls, value: str) -> str:
        return value.strip()


class PushDeviceUnregisterRequest(BaseModel):
    device_token: str = Field(min_length=32, max_length=255)

    @field_validator("device_token")
    @classmethod
    def normalize_device_token(cls, value: str) -> str:
        return value.strip()


class PushDeviceOut(BaseModel):
    id: int
    family_id: int
    user_id: int
    device_token: str = Field(description="Maskierter Device-Token (nicht der vollständige Wert)")
    platform: str
    bundle_id: str
    push_environment: str
    notifications_enabled: bool
    child_new_task: bool
    manager_task_submitted: bool
    manager_reward_requested: bool
    task_due_reminder: bool
    last_seen_at: datetime

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
    email: EmailStr | None
    ha_notify_service: str | None = None
    ha_notifications_enabled: bool = False
    ha_child_new_task: bool = True
    ha_manager_task_submitted: bool = True
    ha_manager_reward_requested: bool = True
    ha_task_due_reminder: bool = True
    is_active: bool
    role: RoleEnum
    created_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr | None = None
    display_name: str = Field(min_length=2, max_length=120)
    ha_notify_service: str | None = Field(default=None, max_length=255)
    ha_notifications_enabled: bool = False
    ha_child_new_task: bool = True
    ha_manager_task_submitted: bool = True
    ha_manager_reward_requested: bool = True
    ha_task_due_reminder: bool = True
    password: str | None = Field(default=None, min_length=3, max_length=128)
    password_confirm: str | None = Field(default=None, min_length=3, max_length=128)
    role: RoleEnum

    @field_validator("ha_notify_service", mode="before")
    @classmethod
    def normalize_ha_notify_service(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.password != self.password_confirm:
            raise ValueError("Passwort und Passwort-Wiederholung stimmen nicht überein")
        return self


class MemberUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    ha_notify_service: str | None = Field(default=None, max_length=255)
    ha_notifications_enabled: bool | None = None
    ha_child_new_task: bool | None = None
    ha_manager_task_submitted: bool | None = None
    ha_manager_reward_requested: bool | None = None
    ha_task_due_reminder: bool | None = None
    role: RoleEnum
    is_active: bool = True
    password: str | None = Field(default=None, min_length=3, max_length=128)

    @field_validator("ha_notify_service", mode="before")
    @classmethod
    def normalize_ha_notify_service(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


ALLOWED_TASK_REMINDER_MINUTES = {15, 30, 60, 120, 1440, 2880}
ALLOWED_DAILY_REMINDER_MINUTES = {15, 30, 60, 120}
ALLOWED_WEEKDAYS = {0, 1, 2, 3, 4, 5, 6}
FULL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]


def _normalize_task_reminders(value: list[int]) -> list[int]:
    unique_sorted = sorted(set(value))
    invalid = [entry for entry in unique_sorted if entry not in ALLOWED_TASK_REMINDER_MINUTES]
    if invalid:
        allowed = ", ".join(str(entry) for entry in sorted(ALLOWED_TASK_REMINDER_MINUTES))
        raise ValueError(f"Ungültige Erinnerungszeiten: {invalid}. Erlaubt sind: {allowed}")
    return unique_sorted


def _normalize_weekdays(value: list[int]) -> list[int]:
    unique_sorted = sorted(set(value))
    invalid = [entry for entry in unique_sorted if entry not in ALLOWED_WEEKDAYS]
    if invalid:
        raise ValueError("Ungültige Wochentage. Erlaubt sind 0=Mo bis 6=So")
    return unique_sorted


def _normalize_due_time_hhmm(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("Uhrzeit muss im Format HH:MM angegeben werden")
    hour, minute = parts
    if not hour.isdigit() or not minute.isdigit():
        raise ValueError("Uhrzeit muss im Format HH:MM angegeben werden")
    hour_value = int(hour)
    minute_value = int(minute)
    if hour_value < 0 or hour_value > 23 or minute_value < 0 or minute_value > 59:
        raise ValueError("Uhrzeit muss zwischen 00:00 und 23:59 liegen")
    return f"{hour_value:02d}:{minute_value:02d}"


class TaskCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    assignee_id: int
    due_at: datetime | None = None
    points: int = Field(default=0, ge=0)
    reminder_offsets_minutes: list[int] = Field(default_factory=list)
    active_weekdays: list[int] = Field(default_factory=list)
    recurrence_type: RecurrenceTypeEnum = RecurrenceTypeEnum.none
    always_submittable: bool = False
    penalty_enabled: bool = False
    penalty_points: int = Field(default=0, ge=0, le=9999)

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def validate_reminder_offsets_minutes(cls, value: list[int]) -> list[int]:
        return _normalize_task_reminders(value)

    @field_validator("active_weekdays")
    @classmethod
    def validate_active_weekdays(cls, value: list[int]) -> list[int]:
        return _normalize_weekdays(value)

    @model_validator(mode="after")
    def validate_task_schedule(self):
        if self.recurrence_type == RecurrenceTypeEnum.daily:
            if not self.due_at:
                raise ValueError("Bei täglicher Wiederholung ist eine Uhrzeit erforderlich")
            if not self.active_weekdays:
                raise ValueError("Bei täglicher Wiederholung muss mindestens ein Wochentag gewählt sein")
            invalid_daily = [entry for entry in self.reminder_offsets_minutes if entry not in ALLOWED_DAILY_REMINDER_MINUTES]
            if invalid_daily:
                raise ValueError("Bei täglicher Wiederholung sind nur Erinnerungen bis 2 Stunden erlaubt")
        elif self.recurrence_type == RecurrenceTypeEnum.monthly:
            if self.due_at is None:
                raise ValueError("Bei monatlicher Wiederholung ist eine Fälligkeit erforderlich")
            if self.reminder_offsets_minutes and self.due_at is None:
                raise ValueError("Erinnerungen benötigen eine Fälligkeit")
            self.active_weekdays = []
        elif self.recurrence_type == RecurrenceTypeEnum.weekly and self.due_at is None:
            if self.reminder_offsets_minutes:
                raise ValueError("Für wöchentliche Aufgaben ohne festen Zeitpunkt sind keine Erinnerungen erlaubt")
            if self.penalty_enabled:
                raise ValueError("Minuspunkte benötigen bei wöchentlichen Aufgaben einen festen Zeitpunkt")
            self.always_submittable = False
            self.active_weekdays = []
        else:
            self.active_weekdays = []

        if self.recurrence_type == RecurrenceTypeEnum.none and self.reminder_offsets_minutes and self.due_at is None:
            raise ValueError("Erinnerungen benötigen eine Fälligkeit")

        if self.recurrence_type not in {RecurrenceTypeEnum.daily, RecurrenceTypeEnum.weekly}:
            self.penalty_enabled = False
            self.penalty_points = 0
        elif self.penalty_enabled and self.penalty_points < 1:
            raise ValueError("Minuspunkte müssen größer als 0 sein")
        return self


class TaskUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    assignee_id: int
    due_at: datetime | None = None
    points: int = Field(default=0, ge=0)
    reminder_offsets_minutes: list[int] = Field(default_factory=list)
    active_weekdays: list[int] = Field(default_factory=list)
    recurrence_type: RecurrenceTypeEnum = RecurrenceTypeEnum.none
    always_submittable: bool = False
    penalty_enabled: bool = False
    penalty_points: int = Field(default=0, ge=0, le=9999)
    is_active: bool = True
    status: TaskStatusEnum = TaskStatusEnum.open

    @field_validator("reminder_offsets_minutes")
    @classmethod
    def validate_reminder_offsets_minutes(cls, value: list[int]) -> list[int]:
        return _normalize_task_reminders(value)

    @field_validator("active_weekdays")
    @classmethod
    def validate_active_weekdays(cls, value: list[int]) -> list[int]:
        return _normalize_weekdays(value)

    @model_validator(mode="after")
    def validate_task_schedule(self):
        if self.recurrence_type == RecurrenceTypeEnum.daily:
            if not self.due_at:
                raise ValueError("Bei täglicher Wiederholung ist eine Uhrzeit erforderlich")
            if not self.active_weekdays:
                raise ValueError("Bei täglicher Wiederholung muss mindestens ein Wochentag gewählt sein")
            invalid_daily = [entry for entry in self.reminder_offsets_minutes if entry not in ALLOWED_DAILY_REMINDER_MINUTES]
            if invalid_daily:
                raise ValueError("Bei täglicher Wiederholung sind nur Erinnerungen bis 2 Stunden erlaubt")
        elif self.recurrence_type == RecurrenceTypeEnum.monthly:
            if self.due_at is None:
                raise ValueError("Bei monatlicher Wiederholung ist eine Fälligkeit erforderlich")
            if self.reminder_offsets_minutes and self.due_at is None:
                raise ValueError("Erinnerungen benötigen eine Fälligkeit")
            self.active_weekdays = []
        elif self.recurrence_type == RecurrenceTypeEnum.weekly and self.due_at is None:
            if self.reminder_offsets_minutes:
                raise ValueError("Für wöchentliche Aufgaben ohne festen Zeitpunkt sind keine Erinnerungen erlaubt")
            if self.penalty_enabled:
                raise ValueError("Minuspunkte benötigen bei wöchentlichen Aufgaben einen festen Zeitpunkt")
            self.always_submittable = False
            self.active_weekdays = []
        else:
            self.active_weekdays = []

        if self.recurrence_type == RecurrenceTypeEnum.none and self.reminder_offsets_minutes and self.due_at is None:
            raise ValueError("Erinnerungen benötigen eine Fälligkeit")

        if self.recurrence_type not in {RecurrenceTypeEnum.daily, RecurrenceTypeEnum.weekly}:
            self.penalty_enabled = False
            self.penalty_points = 0
        elif self.penalty_enabled and self.penalty_points < 1:
            raise ValueError("Minuspunkte müssen größer als 0 sein")
        return self


class TaskOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    assignee_id: int
    due_at: datetime | None
    points: int
    reminder_offsets_minutes: list[int]
    active_weekdays: list[int]
    recurrence_type: RecurrenceTypeEnum
    series_id: str | None
    always_submittable: bool
    penalty_enabled: bool
    penalty_points: int
    penalty_last_applied_at: datetime | None
    special_template_id: int | None
    is_active: bool
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


class MissedTaskReviewRequest(BaseModel):
    action: Literal["delete", "penalty", "approve"]
    comment: str | None = None


class TaskReminderOut(BaseModel):
    task_id: int
    title: str
    assignee_id: int
    due_at: datetime
    reminder_offset_minutes: int
    notify_at: datetime


class TaskActiveUpdate(BaseModel):
    is_active: bool


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
    is_shareable: bool = False
    is_active: bool = True


class RewardUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    cost_points: int = Field(ge=1)
    is_shareable: bool = False
    is_active: bool = True


class RewardOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    cost_points: int
    is_shareable: bool
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


class RewardContributionRequest(BaseModel):
    points: int = Field(ge=1, le=9999)
    comment: str | None = None


class RewardContributionOut(BaseModel):
    id: int
    family_id: int
    reward_id: int
    user_id: int
    points_reserved: int
    status: RewardContributionStatusEnum
    redemption_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RewardContributionProgressItemOut(BaseModel):
    id: int
    user_id: int
    user_name: str
    points_reserved: int
    status: RewardContributionStatusEnum
    created_at: datetime


class RewardContributionProgressOut(BaseModel):
    reward_id: int
    reward_title: str
    cost_points: int
    total_reserved: int
    remaining_points: int
    pending_redemption_id: int | None
    contributions: list[RewardContributionProgressItemOut]


class LedgerEntryOut(BaseModel):
    id: int
    family_id: int
    user_id: int
    user_display_name: str | None = None
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


class PointsTrendBucketOut(BaseModel):
    bucket_key: str
    label: str
    earned_points: int
    spent_points: int
    net_points: int


class PointsRewardRequestStatOut(BaseModel):
    reward_id: int
    reward_title: str
    request_count: int
    approved_count: int
    pending_count: int
    rejected_count: int


class PointsRewardSpendStatOut(BaseModel):
    reward_id: int
    reward_title: str
    points_spent: int
    share_percent: float


class ChildPointsStatsOut(BaseModel):
    family_id: int
    user_id: int
    generated_at: datetime
    current_points: int
    lifetime_earned_points: int
    lifetime_spent_points: int
    average_points_per_day: float
    average_points_per_week: float
    average_points_per_month: float
    active_days: int
    approved_tasks_count: int
    reward_requests_count: int
    reward_contributions_count: int
    trends_daily: list[PointsTrendBucketOut]
    trends_weekly: list[PointsTrendBucketOut]
    trends_monthly: list[PointsTrendBucketOut]
    reward_request_stats: list[PointsRewardRequestStatOut]
    reward_spent_stats: list[PointsRewardSpendStatOut]


class PointsAdjustRequest(BaseModel):
    user_id: int
    points_delta: int = Field(ge=-9999, le=9999)
    description: str = Field(min_length=2, max_length=255)


class SpecialTaskTemplateCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    points: int = Field(default=0, ge=0)
    interval_type: SpecialTaskIntervalEnum
    max_claims_per_interval: int = Field(default=1, ge=1, le=50)
    active_weekdays: list[int] = Field(default_factory=lambda: FULL_WEEKDAYS.copy())
    due_time_hhmm: str | None = Field(default=None, min_length=5, max_length=5)
    is_active: bool = True

    @field_validator("active_weekdays")
    @classmethod
    def validate_active_weekdays(cls, value: list[int]) -> list[int]:
        return _normalize_weekdays(value)

    @field_validator("due_time_hhmm")
    @classmethod
    def validate_due_time_hhmm(cls, value: str | None) -> str | None:
        return _normalize_due_time_hhmm(value)

    @model_validator(mode="after")
    def validate_daily_special_task_fields(self):
        if self.interval_type == SpecialTaskIntervalEnum.daily:
            if not self.active_weekdays:
                raise ValueError("Bei täglichen Sonderaufgaben muss mindestens ein Wochentag gewählt sein")
            if not self.due_time_hhmm:
                raise ValueError("Bei täglichen Sonderaufgaben ist eine Fälligkeitsuhrzeit erforderlich")
        else:
            self.active_weekdays = FULL_WEEKDAYS.copy()
            self.due_time_hhmm = None
        return self


class SpecialTaskTemplateUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    points: int = Field(default=0, ge=0)
    interval_type: SpecialTaskIntervalEnum
    max_claims_per_interval: int = Field(default=1, ge=1, le=50)
    active_weekdays: list[int] = Field(default_factory=lambda: FULL_WEEKDAYS.copy())
    due_time_hhmm: str | None = Field(default=None, min_length=5, max_length=5)
    is_active: bool = True

    @field_validator("active_weekdays")
    @classmethod
    def validate_active_weekdays(cls, value: list[int]) -> list[int]:
        return _normalize_weekdays(value)

    @field_validator("due_time_hhmm")
    @classmethod
    def validate_due_time_hhmm(cls, value: str | None) -> str | None:
        return _normalize_due_time_hhmm(value)

    @model_validator(mode="after")
    def validate_daily_special_task_fields(self):
        if self.interval_type == SpecialTaskIntervalEnum.daily:
            if not self.active_weekdays:
                raise ValueError("Bei täglichen Sonderaufgaben muss mindestens ein Wochentag gewählt sein")
            if not self.due_time_hhmm:
                raise ValueError("Bei täglichen Sonderaufgaben ist eine Fälligkeitsuhrzeit erforderlich")
        else:
            self.active_weekdays = FULL_WEEKDAYS.copy()
            self.due_time_hhmm = None
        return self


class SpecialTaskTemplateOut(BaseModel):
    id: int
    family_id: int
    title: str
    description: str | None
    points: int
    interval_type: SpecialTaskIntervalEnum
    max_claims_per_interval: int
    active_weekdays: list[int]
    due_time_hhmm: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SpecialTaskAvailabilityOut(SpecialTaskTemplateOut):
    used_count: int
    remaining_count: int
    available_now: bool = True
    unavailable_reason: str | None = None


class SystemTestNotificationRequest(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    message: str = Field(min_length=2, max_length=500)
    recipient_user_ids: list[int] | None = None
    test_channel: Literal["active", "sse", "apns", "home_assistant"] = "active"
    send_via_home_assistant: bool = False

    @field_validator("recipient_user_ids")
    @classmethod
    def validate_recipient_user_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None

        normalized: list[int] = []
        for entry in value:
            if entry < 1:
                raise ValueError("Empfänger-IDs müssen größer als 0 sein")
            if entry not in normalized:
                normalized.append(entry)
        return normalized


class SystemTestNotificationOut(BaseModel):
    sent: bool
    family_id: int
    title: str
    message: str
    recipient_count: int
    recipient_user_ids: list[int]
    recipient_display_names: list[str]
    test_channel: Literal["active", "sse", "apns", "home_assistant"]
    delivery_mode: str
    event_type: str
    sent_at: str
    home_assistant_delivery: dict[str, object] | None = None


class SystemRuntimeOut(BaseModel):
    app_name: str
    app_version: str
    app_build_ref: str | None = None
    server_time_utc: datetime


class SystemEventOut(BaseModel):
    id: int
    event_type: str
    payload: dict[str, object] | None = None
    created_at: datetime


class SystemDbDiagnosticsOut(BaseModel):
    duplicate_series_groups: int
    duplicate_series_rows: int
    weekly_flexible_duplicate_groups: int
    weekly_flexible_duplicate_rows: int
    inactive_open_like_count: int
    stale_none_without_due_open_count: int


class SystemDbToolsStatusOut(BaseModel):
    database_engine: str
    backup_supported: bool
    backup_command_available: bool
    restore_command_available: bool
    backup_allowed_dirs: list[str]
    backup_default_dir: str
    backup_timeout_seconds: int
    cleanup_max_passes: int
    diagnostics: SystemDbDiagnosticsOut
    server_time_utc: datetime


class SystemDbDirectoryEntryOut(BaseModel):
    name: str
    path: str


class SystemDbDirectoryBrowseOut(BaseModel):
    allowed_roots: list[str]
    current_path: str
    parent_path: str | None = None
    directories: list[SystemDbDirectoryEntryOut]


class SystemDbDirectoryCreateRequest(BaseModel):
    parent_dir: str = Field(min_length=1, max_length=1024)
    directory_name: str = Field(min_length=1, max_length=120)

    @field_validator("parent_dir", "directory_name")
    @classmethod
    def normalize_directory_fields(cls, value: str) -> str:
        return value.strip()


class SystemDbDirectoryCreateOut(BaseModel):
    created_path: str
    browse: SystemDbDirectoryBrowseOut


class SystemDbBackupRequest(BaseModel):
    target_dir: str | None = Field(default=None, max_length=1024)
    filename_prefix: str = Field(default="homequests", min_length=1, max_length=80)

    @field_validator("target_dir", mode="before")
    @classmethod
    def normalize_target_dir(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SystemDbBackupOut(BaseModel):
    ok: bool
    file_path: str
    file_size_bytes: int
    duration_seconds: float
    created_at_utc: datetime
    database_engine: str


class SystemDbCleanupRequest(BaseModel):
    max_passes: int | None = Field(default=None, ge=1, le=30)


class SystemDbCleanupOut(BaseModel):
    ok: bool
    requested_max_passes: int
    executed_passes: int
    changed_passes: int
    family_id: int
    diagnostics_before: SystemDbDiagnosticsOut
    diagnostics_after: SystemDbDiagnosticsOut
    started_at_utc: datetime
    finished_at_utc: datetime


class SystemDbAnalyzeOut(BaseModel):
    ok: bool
    database_engine: str
    started_at_utc: datetime
    finished_at_utc: datetime


class HomeAssistantSettingsUpdateRequest(BaseModel):
    ha_enabled: bool = False
    notification_channel: NotificationChannelEnum = NotificationChannelEnum.sse
    ha_base_url: str | None = Field(default=None, max_length=255)
    ha_token: str | None = Field(default=None, max_length=4096)
    verify_ssl: bool = True
    keep_existing_token: bool = True

    @field_validator("ha_base_url", "ha_token", mode="before")
    @classmethod
    def normalize_home_assistant_strings(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class HomeAssistantSettingsOut(BaseModel):
    ha_enabled: bool
    notification_channel: NotificationChannelEnum
    ha_base_url: str | None
    verify_ssl: bool
    has_token: bool


class NotificationChannelUpdateRequest(BaseModel):
    channel: NotificationChannelEnum


class HomeAssistantUserConfigUpdateRequest(BaseModel):
    ha_notify_service: str | None = Field(default=None, max_length=255)
    ha_notifications_enabled: bool = False
    ha_child_new_task: bool = True
    ha_manager_task_submitted: bool = True
    ha_manager_reward_requested: bool = True
    ha_task_due_reminder: bool = True

    @field_validator("ha_notify_service", mode="before")
    @classmethod
    def normalize_ha_user_notify_service(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class HomeAssistantUserConfigOut(BaseModel):
    user_id: int
    display_name: str
    role: RoleEnum
    is_active: bool
    ha_notify_service: str | None
    ha_notifications_enabled: bool
    ha_child_new_task: bool
    ha_manager_task_submitted: bool
    ha_manager_reward_requested: bool
    ha_task_due_reminder: bool


class HomeAssistantUserTestRequest(BaseModel):
    title: str = Field(min_length=2, max_length=120, default="Home Assistant Test")
    message: str = Field(min_length=2, max_length=500, default="Dies ist eine Testnachricht aus HomeQuests.")


class SystemPracticalTestRequest(BaseModel):
    scenario: Literal["task_submitted", "task_created", "task_due_reminder"] = "task_created"
    recipient_user_ids: list[int] | None = None
    dry_run: bool = False

    @field_validator("recipient_user_ids")
    @classmethod
    def validate_recipient_user_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None

        normalized: list[int] = []
        for entry in value:
            if entry < 1:
                raise ValueError("Empfänger-IDs müssen größer als 0 sein")
            if entry not in normalized:
                normalized.append(entry)
        return normalized


class SystemPracticalTestOut(BaseModel):
    sent: bool
    dry_run: bool
    family_id: int
    scenario: Literal["task_submitted", "task_created", "task_due_reminder"]
    recipient_user_ids: list[int]
    recipient_display_names: list[str]
    affected_entities: dict[str, object]
    delivery_expectation: str


class AchievementRewardOut(BaseModel):
    kind: AchievementRewardKindEnum
    points: int = 0
    config: dict[str, object] = Field(default_factory=dict)


class AchievementUnlockPresentationOut(BaseModel):
    style: str
    title: str
    subtitle: str
    icon_key: str
    accent_color: str
    haptic: str
    animation: str


class AchievementUnlockEventOut(BaseModel):
    id: int
    achievement_id: int
    user_id: int
    difficulty: AchievementDifficultyEnum
    reward_kind: AchievementRewardKindEnum
    reward_points: int
    presentation_payload: dict[str, object] = Field(default_factory=dict)
    emitted_at: datetime
    displayed_at: datetime | None = None


class AchievementFreezeWindowCreate(BaseModel):
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=255)
    scope: AchievementFreezeScopeEnum = AchievementFreezeScopeEnum.streaks

    @model_validator(mode="after")
    def validate_range(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Freeze-Ende muss nach dem Start liegen")
        return self


class AchievementFreezeWindowOut(BaseModel):
    id: int
    family_id: int
    user_id: int
    scope: AchievementFreezeScopeEnum
    reason: str | None = None
    starts_at: datetime
    ends_at: datetime
    created_by_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AchievementItemOut(BaseModel):
    achievement_id: int
    key: str
    name: str
    description: str
    category: str
    icon_key: str
    difficulty: AchievementDifficultyEnum
    teaser: str | None = None
    status: AchievementProgressStatusEnum
    current_value: int
    target_value: int
    progress_percent: int
    current_streak: int
    best_streak: int
    frozen_periods_used: int
    unlocked_at: datetime | None = None
    profile_claimed_at: datetime | None = None
    reward_granted_at: datetime | None = None
    is_profile_claimable: bool = False
    is_reward_claimable: bool = False
    last_evaluated_at: datetime | None = None
    reward_kind: AchievementRewardKindEnum
    reward_points: int
    reward_config: dict[str, object] = Field(default_factory=dict)
    rule_kind: AchievementRuleKindEnum
    rule_config: dict[str, object] = Field(default_factory=dict)
    progress_payload: dict[str, object] = Field(default_factory=dict)


class AchievementOverviewOut(BaseModel):
    family_id: int
    user_id: int
    user_display_name: str
    total_count: int
    unlocked_count: int
    locked_count: int
    unclaimed_count: int = 0
    reward_pending_count: int = 0
    items: list[AchievementItemOut]
    recent_unlocks: list[AchievementUnlockEventOut]
    freeze_windows: list[AchievementFreezeWindowOut]


class AchievementClaimOut(BaseModel):
    overview: AchievementOverviewOut
    achievement_id: int
    profile_claimed: bool = False
    reward_claimed: bool = False
    points_delta: int = 0
