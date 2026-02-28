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
    base = _as_utc_naive(due_at) or datetime.utcnow()
    if recurrence_type == RecurrenceTypeEnum.daily.value:
        allowed = sorted(set(active_weekdays or [0, 1, 2, 3, 4, 5, 6]))
        candidate = base + timedelta(days=1)
        for _ in range(14):
            if candidate.weekday() in allowed:
                return candidate
            candidate += timedelta(days=1)
        return candidate
    if recurrence_type == RecurrenceTypeEnum.weekly.value:
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

    changed = False
    last_penalty_at = _as_utc_naive(task.penalty_last_applied_at)
    while current_due <= now:
        if not last_penalty_at or last_penalty_at < current_due:
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
            last_penalty_at = current_due
            changed = True
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

        next_due = _next_due(current_due, task.recurrence_type, task.active_weekdays)
        if not next_due or next_due <= current_due:
            break
        current_due = next_due

    if current_due != _as_utc_naive(task.due_at):
        task.due_at = current_due
        changed = True
        emit_live_event(
            db,
            family_id=task.family_id,
            event_type="task.updated",
            payload={"task_id": task.id, "status": task.status.value, "is_active": task.is_active, "assignee_id": task.assignee_id},
        )

    return changed


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


@router.get("/families/{family_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    if _apply_penalties_for_family(db, family_id):
        db.commit()
    return (
        db.query(Task)
        .filter(Task.family_id == family_id)
        .order_by(Task.created_at.desc())
        .all()
    )


@router.get("/families/{family_id}/tasks/reminders/upcoming", response_model=list[TaskReminderOut])
def list_upcoming_task_reminders(
    family_id: int,
    assignee_id: int | None = None,
    window_minutes: int = Query(default=2880, ge=1, le=10080),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    if _apply_penalties_for_family(db, family_id):
        db.commit()
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
            Task.status.in_([TaskStatusEnum.open, TaskStatusEnum.submitted]),
            Task.due_at.is_not(None),
        )
        .order_by(Task.due_at.asc())
    )
    if target_assignee_id is not None:
        query = query.filter(Task.assignee_id == target_assignee_id)

    now = datetime.utcnow()
    window_end = now + timedelta(minutes=window_minutes)
    reminders: list[TaskReminderOut] = []
    for task in query.all():
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
    task.penalty_enabled = payload.penalty_enabled
    task.penalty_points = payload.penalty_points if payload.penalty_enabled else 0
    if not payload.penalty_enabled:
        task.penalty_last_applied_at = None
    task.is_active = payload.is_active
    task.status = payload.status

    # Keep workflow tables and points consistent when admin/parents adjust status manually.
    if old_status == TaskStatusEnum.approved and task.status != TaskStatusEnum.approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bereits bestätigte Aufgaben können nicht auf einen anderen Status zurückgesetzt werden",
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

        if task.recurrence_type != RecurrenceTypeEnum.none.value:
            next_task = Task(
                family_id=task.family_id,
                title=task.title,
                description=task.description,
                assignee_id=task.assignee_id,
                due_at=_next_due(task.due_at, task.recurrence_type, task.active_weekdays),
                points=task.points,
                reminder_offsets_minutes=task.reminder_offsets_minutes,
                active_weekdays=task.active_weekdays,
                recurrence_type=task.recurrence_type,
                penalty_enabled=task.penalty_enabled,
                penalty_points=task.penalty_points,
                penalty_last_applied_at=None,
                is_active=True,
                status=TaskStatusEnum.open,
                created_by_id=current_user.id,
            )
            db.add(next_task)
            db.flush()
            emit_live_event(
                db,
                family_id=task.family_id,
                event_type="task.created",
                payload={"task_id": next_task.id, "assignee_id": next_task.assignee_id},
            )

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
    for template in templates:
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

    used = _special_task_usage_count(db, template.id, current_user.id, template.interval_type)
    if used >= template.max_claims_per_interval:
        if template.interval_type == SpecialTaskIntervalEnum.daily:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tageslimit für diese Sonderaufgabe erreicht")
        if template.interval_type == SpecialTaskIntervalEnum.monthly:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Monatslimit für diese Sonderaufgabe erreicht")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wochenlimit für diese Sonderaufgabe erreicht")

    task = Task(
        family_id=template.family_id,
        title=template.title,
        description=template.description,
        assignee_id=current_user.id,
        due_at=None,
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

    if _apply_penalty_for_task(db, task):
        db.commit()
        db.refresh(task)

    if task.assignee_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur zugewiesenes Familienmitglied darf einreichen")

    if task.status not in {TaskStatusEnum.open, TaskStatusEnum.rejected}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe kann aktuell nicht eingereicht werden")

    if task.recurrence_type == RecurrenceTypeEnum.daily.value:
        allowed_weekdays = set(task.active_weekdays or [])
        if allowed_weekdays and datetime.utcnow().weekday() not in allowed_weekdays:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist heute nicht aktiv")

    if task.due_at and task.due_at > datetime.utcnow():
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

    if task.status == TaskStatusEnum.approved:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe wurde bereits bestätigt")

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

        if task.recurrence_type != RecurrenceTypeEnum.none.value:
            next_task = Task(
                family_id=task.family_id,
                title=task.title,
                description=task.description,
                assignee_id=task.assignee_id,
                due_at=_next_due(task.due_at, task.recurrence_type, task.active_weekdays),
                points=task.points,
                reminder_offsets_minutes=task.reminder_offsets_minutes,
                active_weekdays=task.active_weekdays,
                recurrence_type=task.recurrence_type,
                penalty_enabled=task.penalty_enabled,
                penalty_points=task.penalty_points,
                penalty_last_applied_at=None,
                is_active=True,
                status=TaskStatusEnum.open,
                created_by_id=current_user.id,
            )
            db.add(next_task)
            db.flush()
            emit_live_event(
                db,
                family_id=task.family_id,
                event_type="task.created",
                payload={"task_id": next_task.id, "assignee_id": next_task.assignee_id},
            )
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
