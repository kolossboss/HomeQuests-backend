import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import engine, get_db
from ..deps import get_current_user
from ..models import (
    ApprovalDecisionEnum,
    FamilyMembership,
    PointsLedger,
    PointsSourceEnum,
    RecurrenceTypeEnum,
    RoleEnum,
    SpecialTaskIntervalEnum,
    SpecialTaskTemplate,
    Task,
    TaskApproval,
    TaskGenerationBlock,
    TaskStatusEnum,
    TaskSubmission,
    User,
)
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    MissedTaskReviewRequest,
    SpecialTaskAvailabilityOut,
    SpecialTaskTemplateCreate,
    SpecialTaskTemplateOut,
    SpecialTaskTemplateUpdate,
    TaskActiveUpdate,
    TaskCreate,
    TaskOut,
    TaskReminderOut,
    TaskReviewRequest,
    TaskSubmitRequest,
    TaskUpdate,
)
from ..services import emit_live_event

router = APIRouter(tags=["tasks"])
FULL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]


def _as_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _new_series_id() -> str:
    return uuid4().hex


def _task_event_payload(task: Task, **extra) -> dict:
    due = _as_utc_naive(task.due_at)
    updated = _as_utc_naive(task.updated_at)
    payload = {
        "task_id": task.id,
        "title": task.title,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "is_active": bool(task.is_active),
        "assignee_id": task.assignee_id,
        "due_at": due.isoformat() if due else None,
        "recurrence_type": task.recurrence_type,
        "series_id": task.series_id,
        "active_weekdays": list(task.active_weekdays or []),
        "reminder_offsets_minutes": list(task.reminder_offsets_minutes or []),
        "updated_at": updated.isoformat() if updated else None,
    }
    payload.update(extra)
    return payload


def _add_months(value: datetime, months: int) -> datetime:
    # Simple month-shift with day clamping for shorter months.
    month_index = (value.month - 1) + months
    year = value.year + month_index // 12
    month = (month_index % 12) + 1

    if month == 2:
        leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
        max_day = 29 if leap else 28
    elif month in {4, 6, 9, 11}:
        max_day = 30
    else:
        max_day = 31

    day = min(value.day, max_day)
    return value.replace(year=year, month=month, day=day)


def _next_due(due_at: datetime | None, recurrence_type: str, active_weekdays: list[int] | None = None) -> datetime | None:
    normalized_due = _as_utc_naive(due_at)
    base = normalized_due or datetime.utcnow()
    if recurrence_type == RecurrenceTypeEnum.daily.value:
        allowed = sorted(set(active_weekdays or [0, 1, 2, 3, 4, 5, 6]))
        candidate = base + timedelta(days=1)
        for _ in range(14):
            if candidate.weekday() in allowed:
                return candidate
            candidate += timedelta(days=1)
        return candidate
    if recurrence_type == RecurrenceTypeEnum.weekly.value:
        # "Ganze Woche verfügbar" nutzt due_at=None und darf keinen festen Zeitpunkt erzeugen.
        if normalized_due is None:
            return None
        return base + timedelta(days=7)
    if recurrence_type == RecurrenceTypeEnum.monthly.value:
        return _add_months(base, 1)
    return None


def _start_of_week(value: datetime) -> datetime:
    normalized = _as_utc_naive(value) or datetime.utcnow()
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=normalized.weekday())


def _is_weekly_flexible_task(task: Task) -> bool:
    return task.recurrence_type == RecurrenceTypeEnum.weekly.value and _as_utc_naive(task.due_at) is None


def _align_due_for_active_task(
    due_at: datetime | None,
    recurrence_type: str,
    active_weekdays: list[int] | None = None,
) -> datetime | None:
    due_at = _as_utc_naive(due_at)
    if not due_at or recurrence_type == RecurrenceTypeEnum.none.value:
        return due_at

    now = datetime.utcnow()
    candidate = due_at
    for _ in range(370):
        if candidate > now:
            return candidate
        next_candidate = _next_due(candidate, recurrence_type, active_weekdays)
        if not next_candidate or next_candidate == candidate:
            return candidate
        candidate = next_candidate
    return candidate


def _ensure_assignee_in_family(db: Session, family_id: int, assignee_id: int) -> None:
    assignee_membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == assignee_id)
        .first()
    )
    if not assignee_membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zugewiesener Benutzer ist nicht in der Familie")


def _interval_start(interval_type: SpecialTaskIntervalEnum) -> datetime:
    now = datetime.utcnow()
    if interval_type == SpecialTaskIntervalEnum.daily:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval_type == SpecialTaskIntervalEnum.monthly:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # ISO week starts Monday.
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _normalize_special_weekdays(weekdays: list[int] | None) -> list[int]:
    if not weekdays:
        return FULL_WEEKDAYS.copy()
    normalized = sorted(set(int(value) for value in weekdays if isinstance(value, int)))
    valid = [value for value in normalized if 0 <= value <= 6]
    return valid or FULL_WEEKDAYS.copy()


def _parse_due_time_hhmm(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    hour_raw, minute_raw = parts
    if not hour_raw.isdigit() or not minute_raw.isdigit():
        return None
    hour = int(hour_raw)
    minute = int(minute_raw)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour, minute


def _special_task_due_at_today(template: SpecialTaskTemplate, now: datetime | None = None) -> datetime | None:
    now_value = now or datetime.utcnow()
    parsed = _parse_due_time_hhmm(template.due_time_hhmm)
    if not parsed:
        return None
    hour, minute = parsed
    return now_value.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _special_task_is_available_now(
    template: SpecialTaskTemplate,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    now_value = now or datetime.utcnow()
    if template.interval_type != SpecialTaskIntervalEnum.daily:
        return True, None

    allowed_weekdays = _normalize_special_weekdays(template.active_weekdays)
    if now_value.weekday() not in allowed_weekdays:
        return False, "Sonderaufgabe ist heute nicht verfügbar"

    due_at_today = _special_task_due_at_today(template, now_value)
    if due_at_today and now_value > due_at_today:
        return False, "Sonderaufgabe ist für heute nicht mehr verfügbar"

    return True, None


def _special_task_usage_count(
    db: Session,
    template_id: int,
    interval_type: SpecialTaskIntervalEnum,
) -> int:
    start = _interval_start(interval_type)
    return (
        db.query(Task)
        .filter(
            Task.special_template_id == template_id,
            Task.created_at >= start,
        )
        .count()
    )


def _lock_special_task_claim_window(db: Session, template_id: int) -> None:
    if engine.dialect.name != "postgresql":
        return
    # Verhindert parallele Claim-Races pro Vorlage über mehrere Worker/Instanzen.
    lock_key = 870000000 + int(template_id)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


def _apply_penalty_for_task(db: Session, task: Task) -> bool:
    if not task.is_active:
        return False
    if task.status not in {TaskStatusEnum.open, TaskStatusEnum.rejected}:
        return False
    if task.recurrence_type not in {RecurrenceTypeEnum.daily.value, RecurrenceTypeEnum.weekly.value}:
        return False
    if not task.penalty_enabled or task.penalty_points <= 0:
        return False
    if not task.due_at:
        return False

    now = datetime.utcnow()
    current_due = _as_utc_naive(task.due_at)
    if not current_due:
        return False

    if current_due > now:
        return False

    last_penalty_at = _as_utc_naive(task.penalty_last_applied_at)
    if last_penalty_at and last_penalty_at >= current_due:
        return False

    db.add(
        PointsLedger(
            family_id=task.family_id,
            user_id=task.assignee_id,
            source_type=PointsSourceEnum.task_penalty,
            source_id=task.id,
            points_delta=-task.penalty_points,
            description=f"Minuspunkte (nicht erledigt): {task.title}",
            created_by_id=None,
        )
    )
    task.penalty_last_applied_at = current_due
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="points.adjusted",
        payload={
            "user_id": task.assignee_id,
            "points_delta": -task.penalty_points,
            "task_id": task.id,
            "reason": "task_penalty",
        },
    )
    return True


def _apply_penalties_for_family(db: Session, family_id: int) -> bool:
    tasks = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.recurrence_type.in_([RecurrenceTypeEnum.daily.value, RecurrenceTypeEnum.weekly.value]),
            Task.penalty_enabled == True,  # noqa: E712
            Task.penalty_points > 0,
            Task.due_at.is_not(None),
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected]),
        )
        .all()
    )

    changed = False
    for task in tasks:
        changed = _apply_penalty_for_task(db, task) or changed
    return changed


def _task_schedule_signature(task: Task) -> tuple:
    due = _as_utc_naive(task.due_at)
    if task.recurrence_type == RecurrenceTypeEnum.daily.value:
        weekdays = tuple(sorted(int(value) for value in (task.active_weekdays or []) if isinstance(value, int)))
        if due is None:
            return ("daily", weekdays, "no_due")
        return ("daily", weekdays, due.hour, due.minute)
    if task.recurrence_type == RecurrenceTypeEnum.weekly.value:
        if due is None:
            return ("weekly_flexible",)
        return ("weekly_exact", due.weekday(), due.hour, due.minute)
    if task.recurrence_type == RecurrenceTypeEnum.monthly.value:
        if due is None:
            return ("monthly", "no_due")
        return ("monthly", due.day, due.hour, due.minute)
    if due is None:
        return ("no_due",)
    return ("once", due.year, due.month, due.day, due.hour, due.minute)


def _recurring_task_identity_key(task: Task) -> tuple | None:
    if task.recurrence_type == RecurrenceTypeEnum.none.value:
        return None
    if task.series_id:
        return ("series", str(task.series_id))
    if _is_weekly_flexible_task(task):
        # Für "ganze Woche verfügbar" darf eine reine Textanpassung
        # (z. B. Beschreibung) keine neue Wiederholungsserie erzeugen.
        return (
            task.assignee_id,
            task.title.strip().lower(),
            task.recurrence_type,
            int(task.special_template_id or 0),
            "weekly_flexible",
        )
    weekdays = tuple(sorted(int(value) for value in (task.active_weekdays or []) if isinstance(value, int)))
    return (
        task.assignee_id,
        task.title.strip().lower(),
        (task.description or "").strip().lower(),
        task.recurrence_type,
        weekdays,
        int(task.special_template_id or 0),
        _task_schedule_signature(task),
    )


def _recurring_identity_hash(key: tuple | None) -> str | None:
    if key is None:
        return None
    encoded = json.dumps(list(key), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _block_weekly_flexible_generation_for_current_cycle(
    db: Session,
    task: Task,
    blocked_by_user_id: int,
) -> None:
    if not _is_weekly_flexible_task(task):
        return
    key_hash = _recurring_identity_hash(_recurring_task_identity_key(task))
    if key_hash is None:
        return

    now = datetime.utcnow()
    block_until = _start_of_week(now) + timedelta(days=7)
    _upsert_generation_block(
        db,
        family_id=task.family_id,
        key_hash=key_hash,
        block_until=block_until,
        reason="manual_delete",
        created_by_id=blocked_by_user_id,
    )


def _upsert_generation_block(
    db: Session,
    family_id: int,
    key_hash: str,
    block_until: datetime,
    reason: str,
    created_by_id: int | None,
) -> None:
    block = (
        db.query(TaskGenerationBlock)
        .filter(
            TaskGenerationBlock.family_id == family_id,
            TaskGenerationBlock.key_hash == key_hash,
        )
        .first()
    )
    if block:
        if block.block_until < block_until:
            block.block_until = block_until
        block.reason = reason
        block.created_by_id = created_by_id
        return

    db.add(
        TaskGenerationBlock(
            family_id=family_id,
            key_hash=key_hash,
            block_until=block_until,
            reason=reason,
            created_by_id=created_by_id,
        )
    )


def _active_generation_block_hashes(db: Session, family_id: int, now: datetime) -> set[str]:
    # Abgelaufene Sperren werden opportunistisch bereinigt.
    db.query(TaskGenerationBlock).filter(
        TaskGenerationBlock.family_id == family_id,
        TaskGenerationBlock.block_until <= now,
    ).delete(synchronize_session=False)

    rows = (
        db.query(TaskGenerationBlock.key_hash)
        .filter(
            TaskGenerationBlock.family_id == family_id,
            TaskGenerationBlock.block_until > now,
        )
        .all()
    )
    return {str(row[0]) for row in rows if row and row[0]}


def _deactivate_current_cycle_weekly_flexible_tasks_by_key_hash(
    db: Session,
    family_id: int,
    key_hash: str,
    exclude_task_id: int | None = None,
) -> bool:
    now = datetime.utcnow()
    week_start = _start_of_week(now)
    week_end = week_start + timedelta(days=7)
    query = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.recurrence_type == RecurrenceTypeEnum.weekly.value,
            Task.due_at.is_(None),
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected]),
            Task.created_at >= week_start,
            Task.created_at < week_end,
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
    )
    if exclude_task_id is not None:
        query = query.filter(Task.id != exclude_task_id)

    changed = False
    for task in query.all():
        task_hash = _recurring_identity_hash(_recurring_task_identity_key(task))
        if task_hash != key_hash:
            continue
        task.is_active = False
        db.flush()
        emit_live_event(
            db,
            family_id=task.family_id,
            event_type="task.updated",
            payload=_task_event_payload(task, reason="series_replaced"),
        )
        changed = True
    return changed


def _next_daily_due_from_now(reference_due: datetime, active_weekdays: list[int] | None, now: datetime) -> datetime | None:
    due = _as_utc_naive(reference_due)
    if due is None:
        return None
    allowed = sorted(set(int(value) for value in (active_weekdays or FULL_WEEKDAYS) if isinstance(value, int)))
    if not allowed:
        allowed = FULL_WEEKDAYS.copy()

    for offset in range(0, 14):
        day = now + timedelta(days=offset)
        candidate = day.replace(hour=due.hour, minute=due.minute, second=due.second, microsecond=0)
        if candidate.weekday() not in allowed:
            continue
        if candidate > now:
            return candidate
    return None


def _realign_daily_tasks_for_family(db: Session, family_id: int) -> bool:
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    tasks = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.recurrence_type == RecurrenceTypeEnum.daily.value,
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected]),
            Task.due_at.is_not(None),
        )
        .all()
    )
    changed = False
    for task in tasks:
        due = _as_utc_naive(task.due_at)
        if due is None:
            continue
        created_at = _as_utc_naive(task.created_at)
        # Keine frisch erzeugten Tages-Folgetasks auf heute zurückziehen.
        if created_at and created_at >= today_start:
            continue
        expected = _next_daily_due_from_now(due, task.active_weekdays, now)
        if expected is None:
            continue

        # Auto-Heal nur bei klarer 1-Tages-Verschiebung:
        # steht aktuell auf "morgen", obwohl heute (mit gleicher Uhrzeit) noch möglich ist.
        if due.date() == tomorrow and expected.date() == today and (due.hour, due.minute) == (expected.hour, expected.minute):
            task.due_at = expected
            db.flush()
            emit_live_event(
                db,
                family_id=task.family_id,
                event_type="task.updated",
                payload=_task_event_payload(task, reason="auto_daily_realign"),
            )
            changed = True

    return changed


def _task_due_sort_value(task: Task) -> datetime:
    due = _as_utc_naive(task.due_at)
    if due is None:
        return datetime.max
    return due


def _dedupe_recurring_tasks_for_reminders(tasks: list[Task]) -> list[Task]:
    fixed: list[Task] = []
    by_key: dict[tuple, Task] = {}

    for task in tasks:
        key = _recurring_task_identity_key(task)
        if key is None:
            fixed.append(task)
            continue

        existing = by_key.get(key)
        if not existing or _task_due_sort_value(task) < _task_due_sort_value(existing):
            by_key[key] = task

    merged = fixed + list(by_key.values())
    merged.sort(key=lambda entry: _task_due_sort_value(entry))
    return merged


def _existing_open_recurring_successor(db: Session, source_task: Task) -> Task | None:
    if source_task.series_id:
        return (
            db.query(Task)
            .filter(
                Task.id != source_task.id,
                Task.family_id == source_task.family_id,
                Task.series_id == source_task.series_id,
                Task.is_active == True,  # noqa: E712
                Task.status.in_(
                    [
                        TaskStatusEnum.open,
                        TaskStatusEnum.rejected,
                        TaskStatusEnum.submitted,
                    ]
                ),
            )
            .order_by(Task.created_at.desc(), Task.id.desc())
            .first()
        )

    key = _recurring_task_identity_key(source_task)
    if key is None:
        return None

    is_weekly_flexible = _is_weekly_flexible_task(source_task)
    query = (
        db.query(Task)
        .filter(
            Task.id != source_task.id,
            Task.family_id == source_task.family_id,
            Task.assignee_id == source_task.assignee_id,
            Task.recurrence_type == source_task.recurrence_type,
            Task.is_active == True,  # noqa: E712
            Task.status.in_(
                [
                    TaskStatusEnum.open,
                    TaskStatusEnum.rejected,
                    TaskStatusEnum.submitted,
                ]
            ),
        )
        .order_by(Task.created_at.desc())
    )
    if is_weekly_flexible:
        query = query.filter(Task.due_at.is_(None))
    else:
        query = query.filter(
            Task.title == source_task.title,
            Task.description == source_task.description,
        )
    if source_task.special_template_id is None:
        query = query.filter(Task.special_template_id.is_(None))
    else:
        query = query.filter(Task.special_template_id == source_task.special_template_id)

    # active_weekdays liegt als JSON vor; serverseitiger JSON-Vergleich ist je nach DB-Typ
    # nicht überall stabil. Daher finale Identitätsprüfung in Python.
    for candidate in query.all():
        if _recurring_task_identity_key(candidate) == key:
            return candidate
    return None


def _next_cycle_boundary(task: Task) -> datetime | None:
    due = _as_utc_naive(task.due_at)
    if due is None:
        return None
    return _next_due(due, task.recurrence_type, task.active_weekdays)


def _rollover_missed_tasks_for_family(db: Session, family_id: int) -> bool:
    now = datetime.utcnow()
    candidates = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.due_at.is_not(None),
            Task.recurrence_type.in_(
                [
                    RecurrenceTypeEnum.daily.value,
                    RecurrenceTypeEnum.weekly.value,
                    RecurrenceTypeEnum.monthly.value,
                ]
            ),
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected]),
        )
        .order_by(Task.due_at.asc(), Task.id.asc())
        .all()
    )

    changed = False
    for task in candidates:
        due = _as_utc_naive(task.due_at)
        if not due or due >= now:
            continue
        boundary = _next_cycle_boundary(task)
        if boundary is None or now < boundary:
            continue

        db.add(
            TaskSubmission(
                task_id=task.id,
                submitted_by_id=task.assignee_id,
                note="Automatisch als verpasst markiert",
            )
        )
        task.status = TaskStatusEnum.missed_submitted
        db.flush()
        emit_live_event(
            db,
            family_id=task.family_id,
            event_type="task.missed_reported",
            payload={"task_id": task.id, "assignee_id": task.assignee_id, "auto": True},
        )
        _create_next_recurring_task(db, task, task.created_by_id)
        changed = True

    return changed


def _create_next_recurring_task(db: Session, source_task: Task, created_by_id: int, *, force: bool = False) -> Task | None:
    if source_task.recurrence_type == RecurrenceTypeEnum.none.value:
        return None
    if _is_weekly_flexible_task(source_task) and not force:
        return None
    if not source_task.series_id:
        source_task.series_id = _new_series_id()
        db.flush()
    if _existing_open_recurring_successor(db, source_task):
        return None
    next_due = _next_due(source_task.due_at, source_task.recurrence_type, source_task.active_weekdays)
    next_task = Task(
        family_id=source_task.family_id,
        title=source_task.title,
        description=source_task.description,
        assignee_id=source_task.assignee_id,
        due_at=next_due,
        points=source_task.points,
        reminder_offsets_minutes=source_task.reminder_offsets_minutes,
        active_weekdays=source_task.active_weekdays,
        recurrence_type=source_task.recurrence_type,
        series_id=source_task.series_id,
        always_submittable=source_task.always_submittable,
        penalty_enabled=source_task.penalty_enabled,
        penalty_points=source_task.penalty_points,
        penalty_last_applied_at=None,
        special_template_id=source_task.special_template_id,
        is_active=True,
        status=TaskStatusEnum.open,
        created_by_id=created_by_id,
    )
    db.add(next_task)
    db.flush()
    emit_live_event(
        db,
        family_id=source_task.family_id,
        event_type="task.created",
        payload=_task_event_payload(
            next_task,
            source_task_id=source_task.id,
            source_recurrence_type=source_task.recurrence_type,
            reason="recurring_next_created",
        ),
    )
    return next_task


def _advance_weekly_flexible_tasks_for_family(db: Session, family_id: int) -> bool:
    now = datetime.utcnow()
    now_week_start = _start_of_week(now)
    blocked_hashes = _active_generation_block_hashes(db, family_id, now)
    raw_tasks = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.recurrence_type == RecurrenceTypeEnum.weekly.value,
            Task.due_at.is_(None),
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.rejected, TaskStatusEnum.approved]),
        )
        .order_by(Task.created_at.asc(), Task.id.asc())
        .all()
    )

    grouped_by_key: dict[tuple, list[Task]] = {}
    for task in raw_tasks:
        key = _recurring_task_identity_key(task)
        if key is None:
            continue
        grouped_by_key.setdefault(key, []).append(task)

    changed = False
    latest_by_key: dict[tuple, Task] = {}
    for key, tasks in grouped_by_key.items():
        tasks.sort(key=lambda entry: (entry.created_at, entry.id))
        current_cycle_open = [
            entry
            for entry in tasks
            if _start_of_week(entry.created_at) == now_week_start
            and entry.status in {TaskStatusEnum.open, TaskStatusEnum.rejected}
            and entry.is_active
        ]
        if len(current_cycle_open) >= 2:
            keeper = max(current_cycle_open, key=lambda entry: (_as_utc_naive(entry.updated_at), entry.id))
            for duplicate in current_cycle_open:
                if duplicate.id == keeper.id:
                    continue
                duplicate.is_active = False
                db.flush()
                emit_live_event(
                    db,
                    family_id=duplicate.family_id,
                    event_type="task.updated",
                    payload=_task_event_payload(duplicate, reason="weekly_duplicate_cleanup"),
                )
                changed = True
            tasks = [entry for entry in tasks if entry.id == keeper.id or entry not in current_cycle_open]

        if len(tasks) >= 2:
            latest = tasks[-1]
            previous = tasks[-2]
            same_cycle = _start_of_week(latest.created_at) == _start_of_week(previous.created_at)
            approval_gap = _as_utc_naive(latest.created_at) - _as_utc_naive(previous.updated_at)
            if (
                same_cycle
                and previous.status == TaskStatusEnum.approved
                and latest.status in {TaskStatusEnum.open, TaskStatusEnum.rejected}
                and timedelta(0) <= approval_gap <= timedelta(minutes=10)
            ):
                latest.is_active = False
                db.flush()
                emit_live_event(
                    db,
                    family_id=latest.family_id,
                    event_type="task.updated",
                    payload=_task_event_payload(latest, reason="weekly_duplicate_cleanup"),
                )
                changed = True
                tasks = tasks[:-1]

        if tasks:
            latest_by_key[key] = tasks[-1]

    for task in latest_by_key.values():
        key_hash = _recurring_identity_hash(_recurring_task_identity_key(task))
        if key_hash and key_hash in blocked_hashes:
            continue
        cycle_start = _start_of_week(task.created_at)
        if cycle_start >= now_week_start:
            continue

        if task.status in {TaskStatusEnum.open, TaskStatusEnum.rejected}:
            db.add(
                TaskSubmission(
                    task_id=task.id,
                    submitted_by_id=task.assignee_id,
                    note="Automatisch als verpasst markiert (Wochenaufgabe)",
                )
            )
            task.status = TaskStatusEnum.missed_submitted
            db.flush()
            emit_live_event(
                db,
                family_id=task.family_id,
                event_type="task.missed_reported",
                payload={"task_id": task.id, "assignee_id": task.assignee_id, "auto": True},
            )
            _create_next_recurring_task(db, task, task.created_by_id, force=True)
            changed = True
            continue

        if task.status == TaskStatusEnum.approved:
            changed = _create_next_recurring_task(db, task, task.created_by_id, force=True) is not None or changed

    return changed


@router.get("/families/{family_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    query = db.query(Task).filter(Task.family_id == family_id)
    if context.role == RoleEnum.child:
        query = query.filter(Task.assignee_id == current_user.id)
    return query.order_by(Task.created_at.desc()).all()


@router.get("/families/{family_id}/tasks/reminders/upcoming", response_model=list[TaskReminderOut])
def list_upcoming_task_reminders(
    family_id: int,
    assignee_id: int | None = None,
    window_minutes: int = Query(default=2880, ge=1, le=10080),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    if context.role == RoleEnum.child:
        target_assignee_id = current_user.id
    else:
        target_assignee_id = assignee_id
        if target_assignee_id is not None:
            _ensure_assignee_in_family(db, family_id, target_assignee_id)

    query = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.status == TaskStatusEnum.open,
            Task.due_at.is_not(None),
        )
        .order_by(Task.due_at.asc())
    )
    if target_assignee_id is not None:
        query = query.filter(Task.assignee_id == target_assignee_id)

    now = datetime.utcnow()
    window_end = now + timedelta(minutes=window_minutes)
    reminders: list[TaskReminderOut] = []
    reminder_tasks = _dedupe_recurring_tasks_for_reminders(query.all())
    for task in reminder_tasks:
        if not task.due_at:
            continue
        allowed_offsets = sorted(set(task.reminder_offsets_minutes or []))
        if task.recurrence_type == RecurrenceTypeEnum.daily.value:
            allowed_offsets = [offset for offset in allowed_offsets if offset in {15, 30, 60, 120}]
        for offset in allowed_offsets:
            notify_at = task.due_at - timedelta(minutes=offset)
            if now <= notify_at <= window_end:
                reminders.append(
                    TaskReminderOut(
                        task_id=task.id,
                        title=task.title,
                        assignee_id=task.assignee_id,
                        due_at=task.due_at,
                        reminder_offset_minutes=offset,
                        notify_at=notify_at,
                    )
                )

    reminders.sort(key=lambda entry: entry.notify_at)
    return reminders


@router.post("/families/{family_id}/tasks", response_model=TaskOut)
def create_task(
    family_id: int,
    payload: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    _ensure_assignee_in_family(db, family_id, payload.assignee_id)

    task = Task(
        family_id=family_id,
        title=payload.title,
        description=payload.description,
        assignee_id=payload.assignee_id,
        due_at=_align_due_for_active_task(
            payload.due_at,
            payload.recurrence_type.value,
            payload.active_weekdays,
        ),
        points=payload.points,
        reminder_offsets_minutes=payload.reminder_offsets_minutes,
        active_weekdays=payload.active_weekdays if payload.recurrence_type == RecurrenceTypeEnum.daily else [],
        recurrence_type=payload.recurrence_type.value,
        series_id=_new_series_id() if payload.recurrence_type != RecurrenceTypeEnum.none else None,
        always_submittable=payload.always_submittable,
        penalty_enabled=payload.penalty_enabled,
        penalty_points=payload.penalty_points if payload.penalty_enabled else 0,
        penalty_last_applied_at=None,
        special_template_id=None,
        is_active=True,
        created_by_id=current_user.id,
    )
    db.add(task)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="task.created",
        payload=_task_event_payload(task, reason="manual_create"),
    )
    db.commit()
    db.refresh(task)
    return task


@router.put("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, task.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    if task.status == TaskStatusEnum.approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bereits bestätigte Aufgaben können nicht mehr bearbeitet werden",
        )

    _ensure_assignee_in_family(db, task.family_id, payload.assignee_id)

    old_status = task.status
    old_weekly_flexible_hash = None
    if _is_weekly_flexible_task(task):
        old_weekly_flexible_hash = _recurring_identity_hash(_recurring_task_identity_key(task))

    task.title = payload.title
    task.description = payload.description
    task.assignee_id = payload.assignee_id
    task.due_at = _align_due_for_active_task(
        payload.due_at,
        payload.recurrence_type.value,
        payload.active_weekdays,
    ) if payload.is_active else payload.due_at
    task.points = payload.points
    task.reminder_offsets_minutes = payload.reminder_offsets_minutes
    task.active_weekdays = payload.active_weekdays if payload.recurrence_type == RecurrenceTypeEnum.daily else []
    task.recurrence_type = payload.recurrence_type.value
    if payload.recurrence_type == RecurrenceTypeEnum.none:
        task.series_id = None
    elif not task.series_id:
        task.series_id = _new_series_id()
    task.always_submittable = payload.always_submittable
    task.penalty_enabled = payload.penalty_enabled
    task.penalty_points = payload.penalty_points if payload.penalty_enabled else 0
    if not payload.penalty_enabled:
        task.penalty_last_applied_at = None
    task.is_active = payload.is_active
    task.status = payload.status

    new_weekly_flexible_hash = None
    if _is_weekly_flexible_task(task):
        new_weekly_flexible_hash = _recurring_identity_hash(_recurring_task_identity_key(task))

    if old_weekly_flexible_hash and old_weekly_flexible_hash != new_weekly_flexible_hash:
        # Beim Umbenennen/Umhängen einer flexiblen Wochenserie die alte Serie stilllegen,
        # damit keine zusätzliche Aufgabe aus der alten Konfiguration erzeugt wird.
        _upsert_generation_block(
            db,
            family_id=task.family_id,
            key_hash=old_weekly_flexible_hash,
            block_until=datetime.utcnow() + timedelta(days=3650),
            reason="series_replaced",
            created_by_id=current_user.id,
        )
        _deactivate_current_cycle_weekly_flexible_tasks_by_key_hash(
            db,
            family_id=task.family_id,
            key_hash=old_weekly_flexible_hash,
            exclude_task_id=task.id,
        )

    if old_status != TaskStatusEnum.submitted and task.status == TaskStatusEnum.submitted:
        db.add(
            TaskSubmission(
                task_id=task.id,
                submitted_by_id=task.assignee_id,
                note="Manuell als erledigt gemeldet",
            )
        )

    if old_status != TaskStatusEnum.approved and task.status == TaskStatusEnum.approved:
        latest_submission = (
            db.query(TaskSubmission)
            .filter(TaskSubmission.task_id == task.id)
            .order_by(TaskSubmission.submitted_at.desc())
            .first()
        )
        if not latest_submission:
            latest_submission = TaskSubmission(
                task_id=task.id,
                submitted_by_id=task.assignee_id,
                note="Manuell eingereicht und bestätigt",
            )
            db.add(latest_submission)
            db.flush()

        approval = TaskApproval(
            submission_id=latest_submission.id,
            reviewed_by_id=current_user.id,
            decision=ApprovalDecisionEnum.approved,
            comment="Manuell bestätigt",
        )
        db.add(approval)
        db.flush()

        if task.points > 0:
            db.add(
                PointsLedger(
                    family_id=task.family_id,
                    user_id=task.assignee_id,
                    source_type=PointsSourceEnum.task_approval,
                    source_id=approval.id,
                    points_delta=task.points,
                    description=f"Punkte für Aufgabe: {task.title}",
                    created_by_id=current_user.id,
                )
            )

        _create_next_recurring_task(db, task, current_user.id)

    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.updated",
        payload=_task_event_payload(task, reason="manual_edit"),
    )
    db.commit()
    db.refresh(task)
    return task


@router.get("/families/{family_id}/special-tasks/templates", response_model=list[SpecialTaskTemplateOut])
def list_special_task_templates(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    return (
        db.query(SpecialTaskTemplate)
        .filter(SpecialTaskTemplate.family_id == family_id)
        .order_by(SpecialTaskTemplate.created_at.desc())
        .all()
    )


@router.post("/families/{family_id}/special-tasks/templates", response_model=SpecialTaskTemplateOut)
def create_special_task_template(
    family_id: int,
    payload: SpecialTaskTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    template = SpecialTaskTemplate(
        family_id=family_id,
        title=payload.title,
        description=payload.description,
        points=payload.points,
        interval_type=payload.interval_type,
        max_claims_per_interval=payload.max_claims_per_interval,
        active_weekdays=payload.active_weekdays if payload.interval_type == SpecialTaskIntervalEnum.daily else FULL_WEEKDAYS.copy(),
        due_time_hhmm=payload.due_time_hhmm if payload.interval_type == SpecialTaskIntervalEnum.daily else None,
        is_active=payload.is_active,
        created_by_id=current_user.id,
    )
    db.add(template)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="special_task_template.created",
        payload={"template_id": template.id},
    )
    db.commit()
    db.refresh(template)
    return template


@router.put("/special-tasks/templates/{template_id}", response_model=SpecialTaskTemplateOut)
def update_special_task_template(
    template_id: int,
    payload: SpecialTaskTemplateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = db.query(SpecialTaskTemplate).filter(SpecialTaskTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sonderaufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, template.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    template.title = payload.title
    template.description = payload.description
    template.points = payload.points
    template.interval_type = payload.interval_type
    template.max_claims_per_interval = payload.max_claims_per_interval
    template.active_weekdays = payload.active_weekdays if payload.interval_type == SpecialTaskIntervalEnum.daily else FULL_WEEKDAYS.copy()
    template.due_time_hhmm = payload.due_time_hhmm if payload.interval_type == SpecialTaskIntervalEnum.daily else None
    template.is_active = payload.is_active

    db.flush()
    emit_live_event(
        db,
        family_id=template.family_id,
        event_type="special_task_template.updated",
        payload={"template_id": template.id},
    )
    db.commit()
    db.refresh(template)
    return template


@router.delete("/special-tasks/templates/{template_id}")
def delete_special_task_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = db.query(SpecialTaskTemplate).filter(SpecialTaskTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sonderaufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, template.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    template_id_value = template.id
    family_id_value = template.family_id
    db.delete(template)
    emit_live_event(
        db,
        family_id=family_id_value,
        event_type="special_task_template.deleted",
        payload={"template_id": template_id_value},
    )
    db.commit()
    return {"deleted": True}


@router.get("/families/{family_id}/special-tasks/available", response_model=list[SpecialTaskAvailabilityOut])
def list_available_special_tasks(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.child})

    templates = (
        db.query(SpecialTaskTemplate)
        .filter(SpecialTaskTemplate.family_id == family_id, SpecialTaskTemplate.is_active == True)  # noqa: E712
        .order_by(SpecialTaskTemplate.title.asc())
        .all()
    )

    result: list[SpecialTaskAvailabilityOut] = []
    now = datetime.utcnow()
    for template in templates:
        available_now, _ = _special_task_is_available_now(template, now)
        if not available_now:
            continue
        used = _special_task_usage_count(db, template.id, template.interval_type)
        remaining = max(template.max_claims_per_interval - used, 0)
        result.append(
            SpecialTaskAvailabilityOut(
                id=template.id,
                family_id=template.family_id,
                title=template.title,
                description=template.description,
                points=template.points,
                interval_type=template.interval_type,
                max_claims_per_interval=template.max_claims_per_interval,
                active_weekdays=_normalize_special_weekdays(template.active_weekdays),
                due_time_hhmm=template.due_time_hhmm,
                is_active=template.is_active,
                created_at=template.created_at,
                updated_at=template.updated_at,
                used_count=used,
                remaining_count=remaining,
            )
        )
    return result


@router.post("/special-tasks/templates/{template_id}/claim", response_model=TaskOut)
def claim_special_task(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = db.query(SpecialTaskTemplate).filter(SpecialTaskTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sonderaufgabe nicht gefunden")
    if not template.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sonderaufgabe ist deaktiviert")

    membership_context = get_membership_or_403(db, template.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.child})

    available_now, unavailability_reason = _special_task_is_available_now(template)
    if not available_now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=unavailability_reason or "Sonderaufgabe ist aktuell nicht verfügbar")

    _lock_special_task_claim_window(db, template.id)
    db.refresh(template)

    used = _special_task_usage_count(db, template.id, template.interval_type)
    if used >= template.max_claims_per_interval:
        if template.interval_type == SpecialTaskIntervalEnum.daily:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tageslimit für diese Sonderaufgabe erreicht")
        if template.interval_type == SpecialTaskIntervalEnum.monthly:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monatslimit für diese Sonderaufgabe erreicht")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wochenlimit für diese Sonderaufgabe erreicht")

    due_at = None
    if template.interval_type == SpecialTaskIntervalEnum.daily:
        due_at = _special_task_due_at_today(template)

    task = Task(
        family_id=template.family_id,
        title=template.title,
        description=template.description,
        assignee_id=current_user.id,
        due_at=due_at,
        points=template.points,
        reminder_offsets_minutes=[],
        active_weekdays=[],
        recurrence_type=RecurrenceTypeEnum.none.value,
        penalty_enabled=False,
        penalty_points=0,
        penalty_last_applied_at=None,
        special_template_id=template.id,
        is_active=True,
        status=TaskStatusEnum.open,
        created_by_id=current_user.id,
    )
    db.add(task)
    db.flush()
    emit_live_event(
        db,
        family_id=template.family_id,
        event_type="task.created",
        payload=_task_event_payload(task, source="special_task", reason="special_claim"),
    )
    db.commit()
    db.refresh(task)
    return task


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, task.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    _block_weekly_flexible_generation_for_current_cycle(db, task, current_user.id)

    task_id_value = task.id
    family_id_value = task.family_id
    db.delete(task)
    emit_live_event(
        db,
        family_id=family_id_value,
        event_type="task.deleted",
        payload={"task_id": task_id_value},
    )
    db.commit()
    return {"deleted": True}


@router.post("/tasks/{task_id}/submit", response_model=TaskOut)
def submit_task(
    task_id: int,
    payload: TaskSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    get_membership_or_403(db, task.family_id, current_user.id)

    if task.assignee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur zugewiesenes Familienmitglied darf einreichen")

    if task.status not in {TaskStatusEnum.open, TaskStatusEnum.rejected}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe kann aktuell nicht eingereicht werden")

    now_utc = datetime.utcnow()
    due_at_utc = _as_utc_naive(task.due_at)

    if task.recurrence_type == RecurrenceTypeEnum.daily.value and not task.always_submittable:
        if due_at_utc is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tägliche Aufgabe hat keine gültige Fälligkeit",
            )

        # Für tägliche Aufgaben gilt: ohne "immer erledigbar" darf nur der
        # aktuell fällige Kalendertag eingereicht werden.
        if due_at_utc.date() != now_utc.date():
            if due_at_utc > now_utc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist noch nicht fällig")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aufgabe ist nicht mehr für heute einreichbar",
            )

        allowed_weekdays = set(task.active_weekdays or [])
        if allowed_weekdays:
            if due_at_utc.weekday() not in allowed_weekdays or now_utc.weekday() not in allowed_weekdays:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist heute nicht aktiv")

    if due_at_utc and not task.always_submittable:
        # Heute fällige Aufgaben dürfen auch vor der Uhrzeit eingereicht werden,
        # aber nicht mehrere Kalendertage im Voraus.
        if due_at_utc > now_utc and due_at_utc.date() != now_utc.date():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist noch nicht fällig")

    submission = TaskSubmission(task_id=task.id, submitted_by_id=current_user.id, note=payload.note)
    db.add(submission)
    task.status = TaskStatusEnum.submitted
    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.submitted",
        payload={"task_id": task.id, "assignee_id": task.assignee_id},
    )
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/report-missed", response_model=TaskOut)
def report_task_missed(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    get_membership_or_403(db, task.family_id, current_user.id)

    if task.assignee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur zugewiesenes Familienmitglied darf melden")
    if task.status not in {TaskStatusEnum.open, TaskStatusEnum.rejected}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe kann aktuell nicht als nicht erledigt gemeldet werden")
    if not task.due_at or task.due_at >= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nur überfällige Aufgaben können als nicht erledigt gemeldet werden")

    db.add(
        TaskSubmission(
            task_id=task.id,
            submitted_by_id=current_user.id,
            note="Nicht erledigt gemeldet",
        )
    )
    task.status = TaskStatusEnum.missed_submitted
    _create_next_recurring_task(db, task, current_user.id)
    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.missed_reported",
        payload={"task_id": task.id, "assignee_id": task.assignee_id},
    )
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/review", response_model=TaskOut)
def review_task(
    task_id: int,
    payload: TaskReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, task.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    latest_submission = (
        db.query(TaskSubmission)
        .filter(TaskSubmission.task_id == task.id)
        .order_by(TaskSubmission.submitted_at.desc())
        .first()
    )
    if not latest_submission:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine Einreichung vorhanden")

    if task.status == TaskStatusEnum.missed_submitted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nicht-erledigt-Meldungen bitte über 'missed-review' bearbeiten")
    if task.status != TaskStatusEnum.submitted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe wartet nicht auf Bestätigung")

    approval = TaskApproval(
        submission_id=latest_submission.id,
        reviewed_by_id=current_user.id,
        decision=payload.decision,
        comment=payload.comment,
    )
    db.add(approval)
    db.flush()

    if payload.decision == ApprovalDecisionEnum.approved:
        task.status = TaskStatusEnum.approved
        if task.points > 0:
            db.add(
                PointsLedger(
                    family_id=task.family_id,
                    user_id=task.assignee_id,
                    source_type=PointsSourceEnum.task_approval,
                    source_id=approval.id,
                    points_delta=task.points,
                    description=f"Punkte für Aufgabe: {task.title}",
                    created_by_id=current_user.id,
                )
            )

        _create_next_recurring_task(db, task, current_user.id)
    else:
        task.status = TaskStatusEnum.rejected

    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.reviewed",
        payload={"task_id": task.id, "status": task.status.value, "assignee_id": task.assignee_id},
    )
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/missed-review")
def review_missed_task(
    task_id: int,
    payload: MissedTaskReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, task.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    if task.status != TaskStatusEnum.missed_submitted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe wartet nicht auf Nicht-erledigt-Prüfung")

    due_at = _as_utc_naive(task.due_at)
    deduction = 0
    if payload.action == "approve":
        latest_submission = (
            db.query(TaskSubmission)
            .filter(TaskSubmission.task_id == task.id)
            .order_by(TaskSubmission.submitted_at.desc())
            .first()
        )
        if not latest_submission:
            latest_submission = TaskSubmission(
                task_id=task.id,
                submitted_by_id=task.assignee_id,
                note="Nachträglich als erledigt bestätigt",
            )
            db.add(latest_submission)
            db.flush()

        approval = TaskApproval(
            submission_id=latest_submission.id,
            reviewed_by_id=current_user.id,
            decision=ApprovalDecisionEnum.approved,
            comment=payload.comment or "Nachträglich bestätigt",
        )
        db.add(approval)
        db.flush()

        task.status = TaskStatusEnum.approved
        if task.points > 0:
            db.add(
                PointsLedger(
                    family_id=task.family_id,
                    user_id=task.assignee_id,
                    source_type=PointsSourceEnum.task_approval,
                    source_id=approval.id,
                    points_delta=task.points,
                    description=f"Punkte für Aufgabe: {task.title}",
                    created_by_id=current_user.id,
                )
            )

        _create_next_recurring_task(db, task, current_user.id)
        db.flush()
        emit_live_event(
            db,
            family_id=task.family_id,
            event_type="task.reviewed",
            payload={"task_id": task.id, "status": task.status.value, "assignee_id": task.assignee_id},
        )
        db.commit()
        db.refresh(task)
        return {"deleted": False, "penalty_applied": 0, "approved": True, "task_id": task.id}

    if payload.action == "penalty":
        deduction = task.penalty_points if task.penalty_points > 0 else max(task.points, 0)
        if due_at and task.penalty_last_applied_at and _as_utc_naive(task.penalty_last_applied_at) and _as_utc_naive(task.penalty_last_applied_at) >= due_at:
            deduction = 0
        if deduction > 0:
            db.add(
                PointsLedger(
                    family_id=task.family_id,
                    user_id=task.assignee_id,
                    source_type=PointsSourceEnum.task_penalty,
                    source_id=task.id,
                    points_delta=-deduction,
                    description=f"Nicht erledigt: {task.title}",
                    created_by_id=current_user.id,
                )
            )
            emit_live_event(
                db,
                family_id=task.family_id,
                event_type="points.adjusted",
                payload={"user_id": task.assignee_id, "points_delta": -deduction, "task_id": task.id, "reason": "task_penalty_manual"},
            )

    _create_next_recurring_task(db, task, current_user.id)

    task_id_value = task.id
    family_id_value = task.family_id
    assignee_id_value = task.assignee_id
    db.delete(task)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id_value,
        event_type="task.deleted",
        payload={"task_id": task_id_value, "assignee_id": assignee_id_value},
    )
    db.commit()
    return {"deleted": True, "penalty_applied": deduction}


@router.post("/tasks/{task_id}/active", response_model=TaskOut)
def set_task_active(
    task_id: int,
    payload: TaskActiveUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aufgabe nicht gefunden")

    membership_context = get_membership_or_403(db, task.family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    task.is_active = payload.is_active
    if task.is_active:
        if task.recurrence_type != RecurrenceTypeEnum.none.value and not task.series_id:
            task.series_id = _new_series_id()
        task.due_at = _align_due_for_active_task(task.due_at, task.recurrence_type, task.active_weekdays)

    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.updated",
        payload=_task_event_payload(task, reason="active_toggle"),
    )
    db.commit()
    db.refresh(task)
    return task
