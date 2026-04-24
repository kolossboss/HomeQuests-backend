from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RoleEnum(str, Enum):
    admin = "admin"
    parent = "parent"
    child = "child"


class TaskStatusEnum(str, Enum):
    open = "open"
    submitted = "submitted"
    missed_submitted = "missed_submitted"
    approved = "approved"
    rejected = "rejected"


class RecurrenceTypeEnum(str, Enum):
    none = "none"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class SpecialTaskIntervalEnum(str, Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class ApprovalDecisionEnum(str, Enum):
    approved = "approved"
    rejected = "rejected"


class RedemptionStatusEnum(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PointsSourceEnum(str, Enum):
    task_approval = "task_approval"
    reward_redemption = "reward_redemption"
    reward_contribution = "reward_contribution"
    task_penalty = "task_penalty"
    manual_adjustment = "manual_adjustment"
    achievement_unlock = "achievement_unlock"


class RewardContributionStatusEnum(str, Enum):
    reserved = "reserved"
    submitted = "submitted"
    released = "released"
    consumed = "consumed"


class NotificationChannelEnum(str, Enum):
    sse = "sse"
    apns = "apns"
    home_assistant = "home_assistant"


class AchievementDifficultyEnum(str, Enum):
    bronze = "bronze"
    silver = "silver"
    gold = "gold"
    platinum = "platinum"
    diamond = "diamond"


class AchievementRuleKindEnum(str, Enum):
    aggregate_count = "aggregate_count"
    streak = "streak"


class AchievementRewardKindEnum(str, Enum):
    points_grant = "points_grant"
    unlock_only = "unlock_only"


class AchievementProgressStatusEnum(str, Enum):
    locked = "locked"
    in_progress = "in_progress"
    unlocked = "unlocked"


class AchievementFreezeScopeEnum(str, Enum):
    streaks = "streaks"


class AchievementTaskOutcomeEnum(str, Enum):
    approved = "approved"
    missed = "missed"


class Family(Base):
    __tablename__ = "families"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    ha_notify_service: Mapped[Optional[str]] = mapped_column(String(255))
    ha_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ha_child_new_task: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ha_manager_task_submitted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ha_manager_reward_requested: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ha_task_due_reminder: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class FamilyMembership(Base):
    __tablename__ = "family_memberships"
    __table_args__ = (UniqueConstraint("family_id", "user_id", name="uq_family_user"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[RoleEnum] = mapped_column(SqlEnum(RoleEnum), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reminder_offsets_minutes: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    active_weekdays: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6], nullable=False)
    recurrence_type: Mapped[str] = mapped_column(String(16), default=RecurrenceTypeEnum.none.value, nullable=False)
    series_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    always_submittable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    penalty_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    penalty_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    penalty_last_applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    special_template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("special_task_templates.id", ondelete="SET NULL"),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    status: Mapped[TaskStatusEnum] = mapped_column(SqlEnum(TaskStatusEnum), default=TaskStatusEnum.open, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TaskGenerationBlock(Base):
    __tablename__ = "task_generation_blocks"
    __table_args__ = (UniqueConstraint("family_id", "key_hash", name="uq_task_generation_block_family_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    block_until: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(120))
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TaskSubmission(Base):
    __tablename__ = "task_submissions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    submitted_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class TaskApproval(Base):
    __tablename__ = "task_approvals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("task_submissions.id", ondelete="CASCADE"), index=True)
    reviewed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    decision: Mapped[ApprovalDecisionEnum] = mapped_column(SqlEnum(ApprovalDecisionEnum), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    responsible_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Reward(Base):
    __tablename__ = "rewards"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    cost_points: Mapped[int] = mapped_column(Integer, nullable=False)
    is_shareable: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RewardRedemption(Base):
    __tablename__ = "reward_redemptions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    reward_id: Mapped[int] = mapped_column(ForeignKey("rewards.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[RedemptionStatusEnum] = mapped_column(SqlEnum(RedemptionStatusEnum), default=RedemptionStatusEnum.pending, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class RewardContribution(Base):
    __tablename__ = "reward_contributions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    reward_id: Mapped[int] = mapped_column(ForeignKey("rewards.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    points_reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RewardContributionStatusEnum] = mapped_column(
        SqlEnum(RewardContributionStatusEnum),
        default=RewardContributionStatusEnum.reserved,
        nullable=False,
    )
    redemption_id: Mapped[Optional[int]] = mapped_column(ForeignKey("reward_redemptions.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PointsLedger(Base):
    __tablename__ = "points_ledger"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[PointsSourceEnum] = mapped_column(SqlEnum(PointsSourceEnum), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    points_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SpecialTaskTemplate(Base):
    __tablename__ = "special_task_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interval_type: Mapped[SpecialTaskIntervalEnum] = mapped_column(SqlEnum(SpecialTaskIntervalEnum), nullable=False)
    max_claims_per_interval: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_weekdays: Mapped[list[int]] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6], nullable=False)
    due_time_hhmm: Mapped[Optional[str]] = mapped_column(String(5))
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class LiveUpdateEvent(Base):
    __tablename__ = "live_update_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class HomeAssistantSettings(Base):
    __tablename__ = "home_assistant_settings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True, unique=True)
    ha_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notification_channel: Mapped[str] = mapped_column(String(32), default=NotificationChannelEnum.sse.value, nullable=False)
    ha_base_url: Mapped[Optional[str]] = mapped_column(String(255))
    ha_token: Mapped[Optional[str]] = mapped_column(Text)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PushDevice(Base):
    __tablename__ = "push_devices"
    __table_args__ = (UniqueConstraint("device_token", name="uq_push_device_token"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_token: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(16), default="ios", nullable=False)
    bundle_id: Mapped[str] = mapped_column(String(255), nullable=False)
    push_environment: Mapped[str] = mapped_column(String(16), default="production", nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    child_new_task: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manager_task_submitted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    manager_reward_requested: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    task_due_reminder: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PushDeliveryLog(Base):
    __tablename__ = "push_delivery_logs"
    __table_args__ = (UniqueConstraint("device_id", "dedupe_key", name="uq_push_device_dedupe"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("push_devices.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    apns_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="sent", nullable=False)
    error_reason: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class HomeAssistantDeliveryLog(Base):
    __tablename__ = "home_assistant_delivery_logs"
    __table_args__ = (UniqueConstraint("family_id", "user_id", "notify_service", "dedupe_key", name="uq_ha_delivery_dedupe"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notify_service: Mapped[str] = mapped_column(String(255), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="sent", nullable=False)
    error_reason: Mapped[Optional[str]] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class AchievementDefinition(Base):
    __tablename__ = "achievement_definitions"
    __table_args__ = (UniqueConstraint("key", name="uq_achievement_definition_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    icon_key: Mapped[str] = mapped_column(String(64), nullable=False, default="award")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    difficulty: Mapped[AchievementDifficultyEnum] = mapped_column(SqlEnum(AchievementDifficultyEnum), nullable=False)
    rule_kind: Mapped[AchievementRuleKindEnum] = mapped_column(SqlEnum(AchievementRuleKindEnum), nullable=False)
    rule_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    reward_kind: Mapped[AchievementRewardKindEnum] = mapped_column(SqlEnum(AchievementRewardKindEnum), nullable=False)
    reward_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    teaser: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AchievementProgress(Base):
    __tablename__ = "achievement_progress"
    __table_args__ = (UniqueConstraint("achievement_id", "user_id", name="uq_achievement_progress_user_achievement"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievement_definitions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[AchievementProgressStatusEnum] = mapped_column(
        SqlEnum(AchievementProgressStatusEnum),
        default=AchievementProgressStatusEnum.locked,
        nullable=False,
    )
    current_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    best_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frozen_periods_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    unlocked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    profile_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reward_granted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AchievementUnlockEvent(Base):
    __tablename__ = "achievement_unlock_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievement_definitions.id", ondelete="CASCADE"), index=True)
    progress_id: Mapped[int] = mapped_column(ForeignKey("achievement_progress.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    difficulty: Mapped[AchievementDifficultyEnum] = mapped_column(SqlEnum(AchievementDifficultyEnum), nullable=False)
    reward_kind: Mapped[AchievementRewardKindEnum] = mapped_column(SqlEnum(AchievementRewardKindEnum), nullable=False)
    reward_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    presentation_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    emitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    displayed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class AchievementFreezeWindow(Base):
    __tablename__ = "achievement_freeze_windows"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scope: Mapped[AchievementFreezeScopeEnum] = mapped_column(
        SqlEnum(AchievementFreezeScopeEnum),
        default=AchievementFreezeScopeEnum.streaks,
        nullable=False,
    )
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AchievementTaskRecord(Base):
    __tablename__ = "achievement_task_records"
    __table_args__ = (UniqueConstraint("task_id", name="uq_achievement_task_record_task"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    task_title: Mapped[str] = mapped_column(String(180), nullable=False)
    special_template_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    recurrence_type: Mapped[str] = mapped_column(String(16), nullable=False, default=RecurrenceTypeEnum.none.value)
    outcome: Mapped[AchievementTaskOutcomeEnum] = mapped_column(SqlEnum(AchievementTaskOutcomeEnum), nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    points_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
