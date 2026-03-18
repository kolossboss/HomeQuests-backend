from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
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
    Task,
    TaskApproval,
    TaskStatusEnum,
    TaskSubmission,
    User,
)
from ..rbac import get_membership_or_403, require_roles
from ..schemas import TaskCreate, TaskOut, TaskReviewRequest, TaskSubmitRequest, TaskUpdate

router = APIRouter(tags=["tasks"])


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


def _next_due(due_at: datetime | None, recurrence_type: str) -> datetime | None:
    base = due_at or datetime.utcnow()
    if recurrence_type == RecurrenceTypeEnum.daily.value:
        return base + timedelta(days=1)
    if recurrence_type == RecurrenceTypeEnum.weekly.value:
        return base + timedelta(days=7)
    if recurrence_type == RecurrenceTypeEnum.monthly.value:
        return _add_months(base, 1)
    return None


def _ensure_assignee_in_family(db: Session, family_id: int, assignee_id: int) -> None:
    assignee_membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == assignee_id)
        .first()
    )
    if not assignee_membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Zugewiesener Benutzer ist nicht in der Familie")


@router.get("/families/{family_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    return (
        db.query(Task)
        .filter(Task.family_id == family_id)
        .order_by(Task.created_at.desc())
        .all()
    )


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
        due_at=payload.due_at,
        points=payload.points,
        recurrence_type=payload.recurrence_type.value,
        created_by_id=current_user.id,
    )
    db.add(task)
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
    task.due_at = payload.due_at
    task.points = payload.points
    task.recurrence_type = payload.recurrence_type.value
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
            db.add(
                Task(
                    family_id=task.family_id,
                    title=task.title,
                    description=task.description,
                    assignee_id=task.assignee_id,
                    due_at=_next_due(task.due_at, task.recurrence_type),
                    points=task.points,
                    recurrence_type=task.recurrence_type,
                    status=TaskStatusEnum.open,
                    created_by_id=current_user.id,
                )
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

    db.delete(task)
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

    if task.status == TaskStatusEnum.approved:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aufgabe ist bereits bestätigt")

    submission = TaskSubmission(task_id=task.id, submitted_by_id=current_user.id, note=payload.note)
    db.add(submission)
    task.status = TaskStatusEnum.submitted
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
                due_at=_next_due(task.due_at, task.recurrence_type),
                points=task.points,
                recurrence_type=task.recurrence_type,
                status=TaskStatusEnum.open,
                created_by_id=current_user.id,
            )
            db.add(next_task)
    else:
        task.status = TaskStatusEnum.rejected

    db.commit()
    db.refresh(task)
    return task
