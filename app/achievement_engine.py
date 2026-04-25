from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .achievement_catalog import sync_achievement_catalog
from .models import (
    AchievementDefinition,
    AchievementFreezeScopeEnum,
    AchievementFreezeWindow,
    AchievementProgress,
    AchievementProgressStatusEnum,
    AchievementRewardKindEnum,
    AchievementRuleKindEnum,
    AchievementTaskOutcomeEnum,
    AchievementTaskRecord,
    AchievementUnlockEvent,
    PointsLedger,
    PointsSourceEnum,
    SpecialTaskTemplate,
    Task,
    TaskStatusEnum,
    User,
)
from .services import emit_live_event

STREAK_FREEZE_SCOPE = AchievementFreezeScopeEnum.streaks
EARNED_POINTS_SOURCES = {
    PointsSourceEnum.task_approval,
    PointsSourceEnum.manual_adjustment,
}


@dataclass
class AchievementComputation:
    status: AchievementProgressStatusEnum
    current_value: int
    target_value: int
    progress_percent: int
    current_streak: int
    best_streak: int
    frozen_periods_used: int
    progress_payload: dict


@dataclass(frozen=True)
class PeriodResult:
    label: str
    success: bool
    frozen: bool
    countable: bool
    current_value: int
    target_value: int


@dataclass
class EvaluationContext:
    family_id: int
    user: User
    task_records: list[AchievementTaskRecord]
    current_tasks: list[Task]
    active_special_template_ids: list[int]
    freeze_windows: list[AchievementFreezeWindow]
    earned_points_total: int
    current_points_balance: int


def ensure_achievement_catalog(db: Session) -> None:
    sync_achievement_catalog(db)


def record_task_outcome(
    db: Session,
    task: Task,
    *,
    outcome: AchievementTaskOutcomeEnum,
    completed_at: datetime | None,
    reviewed_at: datetime | None,
    points_awarded: int,
    metadata: dict | None = None,
) -> AchievementTaskRecord:
    record = db.query(AchievementTaskRecord).filter(AchievementTaskRecord.task_id == task.id).first()
    if record is None:
        record = AchievementTaskRecord(
            family_id=task.family_id,
            user_id=task.assignee_id,
            task_id=task.id,
            task_title=task.title,
        )
        db.add(record)

    proxy_due = task.due_at or completed_at or reviewed_at or task.created_at
    record.family_id = task.family_id
    record.user_id = task.assignee_id
    record.task_title = task.title
    record.special_template_id = task.special_template_id
    record.recurrence_type = task.recurrence_type
    record.outcome = outcome
    record.due_at = proxy_due
    record.completed_at = completed_at
    record.reviewed_at = reviewed_at
    record.points_awarded = int(points_awarded)
    record.metadata_json = dict(metadata or {})
    db.flush()
    return record


def list_freeze_windows(db: Session, family_id: int, user_id: int) -> list[AchievementFreezeWindow]:
    return (
        db.query(AchievementFreezeWindow)
        .filter(
            AchievementFreezeWindow.family_id == family_id,
            AchievementFreezeWindow.user_id == user_id,
        )
        .order_by(AchievementFreezeWindow.starts_at.desc())
        .all()
    )


def evaluate_achievements_for_user(
    db: Session,
    family_id: int,
    user_id: int,
    *,
    triggered_by_id: int | None = None,
    reason: str = "system",
    emit_events: bool = True,
) -> list[AchievementUnlockEvent]:
    ensure_achievement_catalog(db)
    definitions = (
        db.query(AchievementDefinition)
        .filter(AchievementDefinition.is_active == True)  # noqa: E712
        .order_by(AchievementDefinition.sort_order.asc(), AchievementDefinition.id.asc())
        .all()
    )
    if not definitions:
        return []

    unlock_events: list[AchievementUnlockEvent] = []
    now = datetime.utcnow()

    for _ in range(4):
        context = _load_context(db, family_id, user_id)
        progress_rows = {
            row.achievement_id: row
            for row in (
                db.query(AchievementProgress)
                .filter(
                    AchievementProgress.family_id == family_id,
                    AchievementProgress.user_id == user_id,
                )
                .all()
            )
        }
        iteration_unlocked: list[AchievementUnlockEvent] = []

        for definition in definitions:
            progress = progress_rows.get(definition.id)
            if progress is None:
                progress = AchievementProgress(
                    family_id=family_id,
                    achievement_id=definition.id,
                    user_id=user_id,
                )
                db.add(progress)
                db.flush()

            computation = _compute_progress(definition, context, now)
            progress.current_value = computation.current_value
            progress.target_value = computation.target_value
            progress.progress_percent = computation.progress_percent
            progress.current_streak = computation.current_streak
            progress.best_streak = max(progress.best_streak, computation.best_streak)
            progress.frozen_periods_used = computation.frozen_periods_used
            progress.progress_payload = computation.progress_payload
            progress.last_evaluated_at = now
            if progress.unlocked_at is None:
                progress.status = computation.status
            else:
                progress.status = AchievementProgressStatusEnum.unlocked

            if computation.status != AchievementProgressStatusEnum.unlocked or progress.unlocked_at is not None:
                continue

            progress.status = AchievementProgressStatusEnum.unlocked
            progress.unlocked_at = now

            presentation = _build_unlock_presentation(definition)
            unlock_event = AchievementUnlockEvent(
                family_id=family_id,
                achievement_id=definition.id,
                progress_id=progress.id,
                user_id=user_id,
                difficulty=definition.difficulty,
                reward_kind=definition.reward_kind,
                reward_points=_reward_points(definition),
                presentation_payload=presentation,
                emitted_at=now,
            )
            db.add(unlock_event)
            db.flush()

            reward_points = _reward_points(definition)

            if emit_events:
                emit_live_event(
                    db,
                    family_id=family_id,
                    event_type="achievement.unlocked",
                    payload={
                        "unlock_event_id": unlock_event.id,
                        "achievement_id": definition.id,
                        "achievement_key": definition.key,
                        "user_id": user_id,
                        "user_display_name": context.user.display_name,
                        "name": definition.name,
                        "description": definition.description,
                        "difficulty": definition.difficulty.value,
                        "icon_key": definition.icon_key,
                        "reward": {
                            "kind": definition.reward_kind.value,
                            "points": reward_points,
                            "config": definition.reward_config or {},
                        },
                        "presentation": presentation,
                        "reason": reason,
                    },
                )

            iteration_unlocked.append(unlock_event)

        db.flush()
        unlock_events.extend(iteration_unlocked)

        if not iteration_unlocked:
            break
        break

    return unlock_events


def claim_achievement_profile(
    db: Session,
    family_id: int,
    user_id: int,
    achievement_id: int,
    *,
    triggered_by_id: int | None = None,
) -> AchievementProgress:
    definition, progress = _load_unlocked_progress(db, family_id, user_id, achievement_id)
    if progress.profile_claimed_at is None:
        progress.profile_claimed_at = datetime.utcnow()
        db.flush()
        emit_live_event(
            db,
            family_id=family_id,
            event_type="achievement.profile_claimed",
            payload={
                "achievement_id": definition.id,
                "achievement_key": definition.key,
                "user_id": user_id,
                "name": definition.name,
                "icon_key": definition.icon_key,
                "difficulty": definition.difficulty.value,
                "reward_points": _reward_points(definition),
                "triggered_by_id": triggered_by_id,
            },
        )
    return progress


def claim_achievement_reward(
    db: Session,
    family_id: int,
    user_id: int,
    achievement_id: int,
    *,
    triggered_by_id: int | None = None,
) -> tuple[AchievementProgress, int]:
    definition, progress = _load_unlocked_progress(db, family_id, user_id, achievement_id)
    if progress.profile_claimed_at is None:
        raise ValueError("Erfolg muss zuerst ins Profil übernommen werden")

    reward_points = _reward_points(definition)
    if reward_points <= 0 or definition.reward_kind != AchievementRewardKindEnum.points_grant:
        raise ValueError("Dieser Erfolg hat kein Punkte-Geschenk")
    if progress.reward_granted_at is not None:
        return progress, 0

    now = datetime.utcnow()
    progress.reward_granted_at = now
    db.flush()
    db.add(
        PointsLedger(
            family_id=family_id,
            user_id=user_id,
            source_type=PointsSourceEnum.achievement_unlock,
            source_id=achievement_id,
            points_delta=reward_points,
            description=f"Erfolgs-Geschenk: {definition.name}",
            created_by_id=triggered_by_id,
        )
    )
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="achievement.reward_claimed",
        payload={
            "achievement_id": definition.id,
            "achievement_key": definition.key,
            "user_id": user_id,
            "name": definition.name,
            "icon_key": definition.icon_key,
            "difficulty": definition.difficulty.value,
            "points_delta": reward_points,
        },
    )
    emit_live_event(
        db,
        family_id=family_id,
        event_type="points.adjusted",
        payload={
            "user_id": user_id,
            "points_delta": reward_points,
            "achievement_id": definition.id,
            "reason": "achievement_reward_claimed",
        },
    )
    return progress, reward_points


def build_achievement_overview(db: Session, family_id: int, user_id: int) -> dict:
    ensure_achievement_catalog(db)
    evaluate_achievements_for_user(
        db,
        family_id=family_id,
        user_id=user_id,
        emit_events=True,
        reason="overview_refresh",
    )
    db.flush()

    user = db.query(User).filter(User.id == user_id).first()
    definitions = (
        db.query(AchievementDefinition)
        .filter(AchievementDefinition.is_active == True)  # noqa: E712
        .order_by(AchievementDefinition.sort_order.asc(), AchievementDefinition.id.asc())
        .all()
    )
    progress_rows = {
        row.achievement_id: row
        for row in (
            db.query(AchievementProgress)
            .filter(
                AchievementProgress.family_id == family_id,
                AchievementProgress.user_id == user_id,
            )
            .all()
        )
    }
    recent_unlocks = (
        db.query(AchievementUnlockEvent)
        .filter(
            AchievementUnlockEvent.family_id == family_id,
            AchievementUnlockEvent.user_id == user_id,
        )
        .order_by(AchievementUnlockEvent.emitted_at.desc(), AchievementUnlockEvent.id.desc())
        .limit(6)
        .all()
    )
    freezes = list_freeze_windows(db, family_id, user_id)
    context = _load_context(db, family_id, user_id)
    now = datetime.utcnow()

    items: list[dict] = []
    unlocked_count = 0
    for definition in definitions:
        progress = progress_rows.get(definition.id)
        reward_points = _reward_points(definition)
        status = progress.status if progress else AchievementProgressStatusEnum.locked
        display_computation: AchievementComputation | None = None
        if progress is None:
            display_computation = _compute_progress(definition, context, now)
            if display_computation.status == AchievementProgressStatusEnum.unlocked:
                status = AchievementProgressStatusEnum.in_progress
        if status == AchievementProgressStatusEnum.unlocked:
            unlocked_count += 1
        payload = progress.progress_payload if progress else {}
        current_value = progress.current_value if progress else (display_computation.current_value if display_computation else 0)
        target_value = progress.target_value if progress else (
            display_computation.target_value if display_computation else int(definition.rule_config.get("target", 0) or 0)
        )
        progress_percent = progress.progress_percent if progress else (display_computation.progress_percent if display_computation else 0)
        items.append(
            {
                "achievement_id": definition.id,
                "key": definition.key,
                "name": definition.name,
                "description": definition.description,
                "category": definition.category,
                "icon_key": definition.icon_key,
                "difficulty": definition.difficulty,
                "teaser": definition.teaser,
                "status": status,
                "current_value": current_value,
                "target_value": target_value,
                "progress_percent": progress_percent,
                "current_streak": progress.current_streak if progress else 0,
                "best_streak": progress.best_streak if progress else 0,
                "frozen_periods_used": progress.frozen_periods_used if progress else 0,
                "unlocked_at": progress.unlocked_at if progress else None,
                "profile_claimed_at": progress.profile_claimed_at if progress else None,
                "reward_granted_at": progress.reward_granted_at if progress else None,
                "is_profile_claimable": bool(progress and progress.unlocked_at and progress.profile_claimed_at is None),
                "is_reward_claimable": bool(
                    progress
                    and progress.unlocked_at
                    and progress.profile_claimed_at is not None
                    and progress.reward_granted_at is None
                    and reward_points > 0
                ),
                "last_evaluated_at": progress.last_evaluated_at if progress else None,
                "reward_kind": definition.reward_kind,
                "reward_points": reward_points,
                "reward_config": definition.reward_config or {},
                "rule_kind": definition.rule_kind,
                "rule_config": definition.rule_config or {},
                "progress_payload": payload,
            }
        )

    visible_achievement_ids = {int(item["achievement_id"]) for item in items}

    return {
        "family_id": family_id,
        "user_id": user_id,
        "user_display_name": user.display_name if user else f"Nutzer {user_id}",
        "total_count": len(items),
        "unlocked_count": unlocked_count,
        "locked_count": max(len(items) - unlocked_count, 0),
        "unclaimed_count": sum(
            1
            for achievement_id, progress in progress_rows.items()
            if achievement_id in visible_achievement_ids
            and progress.unlocked_at is not None
            and progress.profile_claimed_at is None
        ),
        "reward_pending_count": sum(
            1
            for definition in definitions
            if (
                definition.id in visible_achievement_ids
                and (progress := progress_rows.get(definition.id)) is not None
                and progress.unlocked_at is not None
                and progress.profile_claimed_at is not None
                and progress.reward_granted_at is None
                and _reward_points(definition) > 0
            )
        ),
        "items": items,
        "recent_unlocks": [
            {
                "id": event.id,
                "achievement_id": event.achievement_id,
                "user_id": event.user_id,
                "difficulty": event.difficulty,
                "reward_kind": event.reward_kind,
                "reward_points": event.reward_points,
                "presentation_payload": _normalized_presentation_payload(event.presentation_payload or {}),
                "emitted_at": event.emitted_at,
                "displayed_at": event.displayed_at,
            }
            for event in recent_unlocks
            if event.achievement_id in visible_achievement_ids
        ],
        "freeze_windows": freezes,
    }


def _normalized_presentation_payload(payload: dict) -> dict:
    normalized = dict(payload or {})
    if normalized.get("title") == "Auszeichnung freigeschaltet":
        normalized["title"] = "Erfolg freigeschaltet"
    return normalized


def _load_unlocked_progress(
    db: Session,
    family_id: int,
    user_id: int,
    achievement_id: int,
) -> tuple[AchievementDefinition, AchievementProgress]:
    row = (
        db.query(AchievementDefinition, AchievementProgress)
        .join(AchievementProgress, AchievementProgress.achievement_id == AchievementDefinition.id)
        .filter(
            AchievementDefinition.id == achievement_id,
            AchievementProgress.family_id == family_id,
            AchievementProgress.user_id == user_id,
        )
        .first()
    )
    if row is None:
        raise ValueError("Erfolg nicht gefunden")
    definition, progress = row
    if progress.unlocked_at is None:
        raise ValueError("Erfolg ist noch nicht freigeschaltet")
    return definition, progress


def _load_context(db: Session, family_id: int, user_id: int) -> EvaluationContext:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise ValueError("Nutzer nicht gefunden")

    task_records = (
        db.query(AchievementTaskRecord)
        .filter(
            AchievementTaskRecord.family_id == family_id,
            AchievementTaskRecord.user_id == user_id,
        )
        .order_by(AchievementTaskRecord.reviewed_at.desc(), AchievementTaskRecord.id.desc())
        .all()
    )
    current_tasks = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.assignee_id == user_id,
        )
        .all()
    )
    active_special_template_ids = [
        int(entry[0])
        for entry in (
            db.query(SpecialTaskTemplate.id)
            .filter(
                SpecialTaskTemplate.family_id == family_id,
                SpecialTaskTemplate.is_active == True,  # noqa: E712
            )
            .all()
        )
    ]
    freeze_windows = list_freeze_windows(db, family_id, user_id)
    earned_points_total = _earned_points_total(db, family_id, user_id)
    current_points_balance = _current_points_balance(db, family_id, user_id)
    return EvaluationContext(
        family_id=family_id,
        user=user,
        task_records=task_records,
        current_tasks=current_tasks,
        active_special_template_ids=active_special_template_ids,
        freeze_windows=freeze_windows,
        earned_points_total=earned_points_total,
        current_points_balance=current_points_balance,
    )


def _earned_points_total(db: Session, family_id: int, user_id: int) -> int:
    result = (
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(
            PointsLedger.family_id == family_id,
            PointsLedger.user_id == user_id,
            PointsLedger.source_type.in_(list(EARNED_POINTS_SOURCES)),
            PointsLedger.points_delta > 0,
        )
        .scalar()
    )
    return int(result or 0)


def _current_points_balance(db: Session, family_id: int, user_id: int) -> int:
    result = (
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(
            PointsLedger.family_id == family_id,
            PointsLedger.user_id == user_id,
        )
        .scalar()
    )
    return int(result or 0)


def _compute_progress(definition: AchievementDefinition, context: EvaluationContext, now: datetime) -> AchievementComputation:
    if definition.rule_kind == AchievementRuleKindEnum.aggregate_count:
        return _compute_aggregate_progress(definition, context)
    if definition.rule_kind == AchievementRuleKindEnum.streak:
        return _compute_streak_progress(definition, context, now)
    return AchievementComputation(
        status=AchievementProgressStatusEnum.locked,
        current_value=0,
        target_value=0,
        progress_percent=0,
        current_streak=0,
        best_streak=0,
        frozen_periods_used=0,
        progress_payload={"note": "Regeltyp noch nicht implementiert"},
    )


def _compute_aggregate_progress(definition: AchievementDefinition, context: EvaluationContext) -> AchievementComputation:
    metric = str((definition.rule_config or {}).get("metric") or "").strip()
    target = max(int((definition.rule_config or {}).get("target") or 0), 1)
    approved_records = [record for record in context.task_records if record.outcome == AchievementTaskOutcomeEnum.approved]

    if metric == "earned_points_total":
        current = context.earned_points_total
    elif metric == "current_points_balance":
        current = context.current_points_balance
    elif metric == "approved_tasks_total":
        current = len(approved_records)
    elif metric == "approved_special_tasks_total":
        current = len([record for record in approved_records if record.special_template_id is not None])
    elif metric == "approved_weekly_tasks_total":
        current = len([record for record in approved_records if record.recurrence_type == "weekly"])
    else:
        current = 0

    percent = _percent(current, target)
    status = AchievementProgressStatusEnum.unlocked if current >= target else (
        AchievementProgressStatusEnum.in_progress if current > 0 else AchievementProgressStatusEnum.locked
    )
    return AchievementComputation(
        status=status,
        current_value=current,
        target_value=target,
        progress_percent=percent,
        current_streak=0,
        best_streak=0,
        frozen_periods_used=0,
        progress_payload={
            "metric": metric,
            "summary": f"{current} / {target}",
        },
    )


def _compute_streak_progress(definition: AchievementDefinition, context: EvaluationContext, now: datetime) -> AchievementComputation:
    config = definition.rule_config or {}
    target = max(int(config.get("target") or 0), 1)
    period = str(config.get("period") or "week")
    scan_limit = max(target * 6, 24)
    period_results = [
        _evaluate_period(definition, context, now, offset)
        for offset in range(scan_limit)
    ]

    current_streak = 0
    frozen_periods = 0
    for result in period_results:
        if result.frozen:
            frozen_periods += 1
            continue
        if not result.countable:
            continue
        if result.success:
            current_streak += 1
            continue
        break

    best_streak = 0
    rolling = 0
    for result in period_results:
        if result.frozen or not result.countable:
            continue
        if result.success:
            rolling += 1
            best_streak = max(best_streak, rolling)
        else:
            rolling = 0

    target_value = target
    percent = _percent(current_streak, target_value)
    status = AchievementProgressStatusEnum.unlocked if current_streak >= target_value else (
        AchievementProgressStatusEnum.in_progress if current_streak > 0 else AchievementProgressStatusEnum.locked
    )

    recent = [
        {
            "label": result.label,
            "success": result.success,
            "frozen": result.frozen,
            "countable": result.countable,
            "current_value": result.current_value,
            "target_value": result.target_value,
        }
        for result in period_results[:6]
    ]
    return AchievementComputation(
        status=status,
        current_value=current_streak,
        target_value=target_value,
        progress_percent=percent,
        current_streak=current_streak,
        best_streak=best_streak,
        frozen_periods_used=frozen_periods,
        progress_payload={
            "period": period,
            "metric": config.get("metric"),
            "summary": f"{current_streak} / {target_value} Serien",
            "recent_periods": recent,
        },
    )


def _evaluate_period(definition: AchievementDefinition, context: EvaluationContext, now: datetime, offset: int) -> PeriodResult:
    config = definition.rule_config or {}
    period = str(config.get("period") or "week")
    period_start, period_end, label = _period_bounds(now, period, offset)
    frozen = _period_is_frozen(period_start, period_end, context.freeze_windows)
    if frozen:
        return PeriodResult(label=label, success=False, frozen=True, countable=False, current_value=0, target_value=0)

    metric = str(config.get("metric") or "")
    if metric == "all_active_special_tasks_completed":
        return _evaluate_special_coverage_period(definition, context, period_start, period_end, label)
    return _evaluate_task_period(definition, context, period_start, period_end, label)


def _evaluate_special_coverage_period(
    definition: AchievementDefinition,
    context: EvaluationContext,
    period_start: datetime,
    period_end: datetime,
    label: str,
) -> PeriodResult:
    required_template_ids = set(context.active_special_template_ids)
    if not required_template_ids:
        return PeriodResult(label=label, success=False, frozen=False, countable=False, current_value=0, target_value=1)

    # TODO: Sobald Template-Historie existiert, sollte hier die periodengenaue Aktivität
    # statt des aktuellen Template-Sets verwendet werden.
    completed_template_ids = {
        int(record.special_template_id)
        for record in context.task_records
        if (
            record.outcome == AchievementTaskOutcomeEnum.approved
            and record.special_template_id is not None
            and _task_proxy_due(record) >= period_start
            and _task_proxy_due(record) < period_end
        )
    }
    current = len(completed_template_ids & required_template_ids)
    target = len(required_template_ids)
    success = current >= target and target > 0
    return PeriodResult(
        label=label,
        success=success,
        frozen=False,
        countable=target > 0,
        current_value=current,
        target_value=target,
    )


def _evaluate_task_period(
    definition: AchievementDefinition,
    context: EvaluationContext,
    period_start: datetime,
    period_end: datetime,
    label: str,
) -> PeriodResult:
    config = definition.rule_config or {}
    recurrence_types = set(config.get("recurrence_types") or [])
    minimum_tasks = max(int(config.get("minimum_tasks") or 0), 0)
    require_early = str(config.get("metric") or "") == "all_due_tasks_completed_early"
    cutoff_dt = _completion_cutoff(period_start, config) if require_early else None

    task_ids_with_records: set[int] = set()
    total = 0
    approved = 0
    missed = 0

    for record in context.task_records:
        proxy_due = _task_proxy_due(record)
        if proxy_due < period_start or proxy_due >= period_end:
            continue
        if recurrence_types and record.recurrence_type not in recurrence_types:
            continue
        task_ids_with_records.add(record.task_id)
        total += 1
        if record.outcome == AchievementTaskOutcomeEnum.missed:
            missed += 1
            continue
        if require_early and record.completed_at and cutoff_dt and record.completed_at > cutoff_dt:
            continue
        if require_early and record.completed_at is None:
            continue
        approved += 1

    for task in context.current_tasks:
        if task.id in task_ids_with_records:
            continue
        proxy_due = _task_proxy_due(task)
        if proxy_due < period_start or proxy_due >= period_end:
            continue
        if recurrence_types and task.recurrence_type not in recurrence_types:
            continue
        total += 1
        if _task_status_value(task.status) == TaskStatusEnum.approved.value:
            if require_early and cutoff_dt and _task_proxy_completed(task) and _task_proxy_completed(task) > cutoff_dt:
                continue
            if require_early and _task_proxy_completed(task) is None:
                continue
            approved += 1
            continue
        if _task_status_value(task.status) == TaskStatusEnum.missed_submitted.value and period_end <= datetime.utcnow():
            missed += 1

    if total < minimum_tasks or total == 0:
        return PeriodResult(label=label, success=False, frozen=False, countable=False, current_value=total, target_value=max(minimum_tasks, 1))

    success = missed == 0 and approved >= total
    return PeriodResult(
        label=label,
        success=success,
        frozen=False,
        countable=True,
        current_value=approved,
        target_value=total,
    )


def _period_bounds(now: datetime, period: str, offset: int) -> tuple[datetime, datetime, str]:
    if period == "month":
        anchor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = _shift_month(anchor, -offset)
        end = _shift_month(start, 1)
        return start, end, start.strftime("%m/%Y")

    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    start = week_start - timedelta(weeks=offset)
    end = start + timedelta(days=7)
    iso_year, iso_week, _ = start.isocalendar()
    return start, end, f"KW {iso_week}/{iso_year}"


def _shift_month(value: datetime, months: int) -> datetime:
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = (month_index % 12) + 1
    return value.replace(year=year, month=month, day=1)


def _period_is_frozen(start: datetime, end: datetime, freeze_windows: list[AchievementFreezeWindow]) -> bool:
    for window in freeze_windows:
        if window.scope != STREAK_FREEZE_SCOPE:
            continue
        if window.starts_at < end and window.ends_at >= start:
            return True
    return False


def _completion_cutoff(period_start: datetime, config: dict) -> datetime | None:
    cutoff_weekday = config.get("completion_weekday_cutoff")
    if cutoff_weekday is None:
        return None
    cutoff_hour = int(config.get("completion_hour_cutoff") or 23)
    return period_start + timedelta(days=int(cutoff_weekday), hours=cutoff_hour, minutes=59, seconds=59)


def _percent(current: int, target: int) -> int:
    if target <= 0:
        return 0
    return max(0, min(int((current / target) * 100), 100))


def _reward_points(definition: AchievementDefinition) -> int:
    reward_config = definition.reward_config or {}
    return max(int(reward_config.get("points") or 0), 0)


def _build_unlock_presentation(definition: AchievementDefinition) -> dict:
    accent_map = {
        "bronze": "#b47d49",
        "silver": "#a9b7c9",
        "gold": "#e8b923",
        "platinum": "#73d0e6",
        "diamond": "#b9f2ff",
    }
    difficulty = definition.difficulty.value
    return {
        "style": "celebration_banner",
        "title": "Erfolg freigeschaltet",
        "subtitle": definition.name,
        "icon_key": definition.icon_key,
        "accent_color": accent_map.get(difficulty, "#0a84ff"),
        "haptic": "success",
        "animation": "burst",
    }


def _task_proxy_due(record_or_task: AchievementTaskRecord | Task) -> datetime:
    if isinstance(record_or_task, AchievementTaskRecord):
        return record_or_task.due_at or record_or_task.reviewed_at or record_or_task.completed_at or record_or_task.created_at
    return record_or_task.due_at or record_or_task.created_at


def _task_proxy_completed(task: Task) -> datetime | None:
    if task.updated_at:
        return task.updated_at
    return task.created_at


def _task_status_value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")
