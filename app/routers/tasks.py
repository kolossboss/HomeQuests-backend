from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
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
    assignee_id: int,
    interval_type: SpecialTaskIntervalEnum,
) -> int:
    start = _interval_start(interval_type)
    return (
        db.query(Task)
        .filter(
            Task.special_template_id == template_id,
            Task.assignee_id == assignee_id,
            Task.created_at >= start,
        )
        .count()
    )


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


def _recurring_task_identity_key(task: Task) -> tuple | None:
    if task.recurrence_type == RecurrenceTypeEnum.none.value:
        return None
    weekdays = tuple(sorted(int(value) for value in (task.active_weekdays or []) if isinstance(value, int)))
    return (
        task.assignee_id,
        task.title.strip().lower(),
        (task.description or "").strip().lower(),
        task.recurrence_type,
        weekdays,
        int(task.special_template_id or 0),
    )


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
    key = _recurring_task_identity_key(source_task)
    if key is None:
        return None

    query = (
        db.query(Task)
        .filter(
            Task.id != source_task.id,
            Task.family_id == source_task.family_id,
            Task.assignee_id == source_task.assignee_id,
            Task.recurrence_type == source_task.recurrence_type,
            Task.title == source_task.title,
            Task.description == source_task.description,
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
    if source_task.special_template_id is None:
        query = query.filter(Task.special_template_id.is_(None))
    else:
        query = query.filter(Task.special_template_id == source_task.special_template_id)

    # active_weekdays liegt als JSON vor; serverseitiger JSON-Vergleich ist je nach DB-Typ
    # nicht überall stabil. Daher finale Identitätsprüfung in Python.
    for candidate in query.limit(200).all():
        if _recurring_task_identity_key(candidate) == key:
            return candidate
    return None


def _next_cycle_boundary(task: Task) -> datetime | None:
    due = _as_utc_naive(task.due_at)
    if due is None:
        return None

    if task.recurrence_type == RecurrenceTypeEnum.daily.value:
        return due.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    if task.recurrence_type == RecurrenceTypeEnum.weekly.value:
        week_start = due.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=due.weekday())
        return week_start + timedelta(days=7)

    if task.recurrence_type == RecurrenceTypeEnum.monthly.value:
        month_start = due.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return _add_months(month_start, 1)

    return None


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


def _create_next_recurring_task(db: Session, source_task: Task, created_by_id: int) -> Task | None:
    if source_task.recurrence_type == RecurrenceTypeEnum.none.value:
        return None
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
        payload={"task_id": next_task.id, "assignee_id": next_task.assignee_id},
    )
    return next_task


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
        payload={"task_id": task.id, "assignee_id": task.assignee_id},
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
    task.always_submittable = payload.always_submittable
    task.penalty_enabled = payload.penalty_enabled
    task.penalty_points = payload.penalty_points if payload.penalty_enabled else 0
    if not payload.penalty_enabled:
        task.penalty_last_applied_at = None
    task.is_active = payload.is_active
    task.status = payload.status

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
        payload={"task_id": task.id, "status": task.status.value, "is_active": task.is_active, "assignee_id": task.assignee_id},
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
        used = _special_task_usage_count(db, template.id, current_user.id, template.interval_type)
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

    used = _special_task_usage_count(db, template.id, current_user.id, template.interval_type)
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
        payload={"task_id": task.id, "assignee_id": task.assignee_id, "source": "special_task"},
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

    if task.recurrence_type == RecurrenceTypeEnum.daily.value and not task.always_submittable:
        allowed_weekdays = set(task.active_weekdays or [])
        if allowed_weekdays and datetime.utcnow().weekday() not in allowed_weekdays:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist heute nicht aktiv")

    if task.due_at and not task.always_submittable:
        now_utc = datetime.utcnow()
        # Heute fällige Aufgaben dürfen als erledigt gemeldet werden.
        if task.due_at > now_utc and task.due_at.date() != now_utc.date():
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
        task.due_at = _align_due_for_active_task(task.due_at, task.recurrence_type, task.active_weekdays)

    db.flush()
    emit_live_event(
        db,
        family_id=task.family_id,
        event_type="task.updated",
        payload={"task_id": task.id, "status": task.status.value, "is_active": task.is_active, "assignee_id": task.assignee_id},
    )
    db.commit()
    db.refresh(task)
    return task
