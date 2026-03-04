from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import FamilyMembership, RecurrenceTypeEnum, RoleEnum, Task, TaskStatusEnum, TaskSubmission, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    SystemPracticalTestOut,
    SystemPracticalTestRequest,
    SystemTestNotificationOut,
    SystemTestNotificationRequest,
)
from ..services import emit_live_event

router = APIRouter(tags=["system"])


@router.post("/families/{family_id}/system/test-notification", response_model=SystemTestNotificationOut)
def send_system_test_notification(
    family_id: int,
    payload: SystemTestNotificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    recipients = (
        db.query(User)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(
            FamilyMembership.family_id == family_id,
            User.is_active == True,  # noqa: E712
        )
        .order_by(User.display_name.asc())
        .all()
    )
    if not recipients:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine aktiven Nutzer gefunden")

    recipients_by_id = {entry.id: entry for entry in recipients}
    if payload.recipient_user_ids is None:
        selected_recipients = recipients
    else:
        missing_user_ids = [entry for entry in payload.recipient_user_ids if entry not in recipients_by_id]
        if missing_user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ungültige Empfänger-ID(s): {missing_user_ids}",
            )
        selected_recipients = [recipients_by_id[entry] for entry in payload.recipient_user_ids]

    if not selected_recipients:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mindestens ein Empfänger muss ausgewählt sein")

    recipient_user_ids = [entry.id for entry in selected_recipients]
    recipient_display_names = [entry.display_name for entry in selected_recipients]
    sent_at = datetime.utcnow().isoformat()

    emit_live_event(
        db,
        family_id=family_id,
        event_type="notification.test",
        payload={
            "title": payload.title,
            "message": payload.message,
            "requested_by_id": current_user.id,
            "recipient_user_ids": recipient_user_ids,
            "sent_at": sent_at,
        },
    )
    db.commit()

    return SystemTestNotificationOut(
        sent=True,
        family_id=family_id,
        title=payload.title,
        message=payload.message,
        recipient_count=len(recipient_user_ids),
        recipient_user_ids=recipient_user_ids,
        recipient_display_names=recipient_display_names,
        delivery_mode="live_event",
        event_type="notification.test",
        sent_at=sent_at,
    )


@router.post("/families/{family_id}/system/test-notification/practical", response_model=SystemPracticalTestOut)
def send_system_practical_test_notification(
    family_id: int,
    payload: SystemPracticalTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    members = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(
            FamilyMembership.family_id == family_id,
            User.is_active == True,  # noqa: E712
        )
        .all()
    )
    if not members:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine aktiven Nutzer gefunden")

    members_by_user_id = {user.id: (membership, user) for membership, user in members}
    manager_members = [
        (membership, user)
        for membership, user in members
        if membership.role in {RoleEnum.admin, RoleEnum.parent}
    ]
    child_members = [
        (membership, user)
        for membership, user in members
        if membership.role == RoleEnum.child
    ]

    if payload.recipient_user_ids is None:
        if payload.scenario == "task_submitted":
            selected_recipients = manager_members
        else:
            selected_recipients = child_members
    else:
        missing_user_ids = [entry for entry in payload.recipient_user_ids if entry not in members_by_user_id]
        if missing_user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ungültige Empfänger-ID(s): {missing_user_ids}",
            )
        selected_recipients = [members_by_user_id[entry] for entry in payload.recipient_user_ids]

    if not selected_recipients:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mindestens ein Empfänger muss ausgewählt sein")

    recipient_user_ids = [user.id for _, user in selected_recipients]
    recipient_display_names = [user.display_name for _, user in selected_recipients]
    now = datetime.utcnow()

    def create_test_task_for_user(
        assignee_user_id: int,
        title: str,
        description: str,
        due_at: datetime | None,
        reminder_offsets_minutes: list[int],
    ) -> Task:
        task = Task(
            family_id=family_id,
            title=title,
            description=description,
            assignee_id=assignee_user_id,
            due_at=due_at,
            points=0,
            reminder_offsets_minutes=reminder_offsets_minutes,
            active_weekdays=[],
            recurrence_type=RecurrenceTypeEnum.none.value,
            penalty_enabled=False,
            penalty_points=0,
            penalty_last_applied_at=None,
            special_template_id=None,
            is_active=True,
            status=TaskStatusEnum.open,
            created_by_id=current_user.id,
        )
        db.add(task)
        db.flush()
        emit_live_event(
            db,
            family_id=family_id,
            event_type="task.created",
            payload={"task_id": task.id, "assignee_id": task.assignee_id, "source": "system_practical_test"},
        )
        return task

    if payload.scenario == "task_submitted":
        if not child_members:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Für das Szenario 'task_submitted' ist mindestens ein aktives Kind erforderlich",
            )
        invalid_roles = [
            user.display_name
            for membership, user in selected_recipients
            if membership.role not in {RoleEnum.admin, RoleEnum.parent}
        ]
        if invalid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Szenario 'task_submitted' richtet sich an Eltern/Admin. "
                    f"Ungültige Empfänger: {invalid_roles}"
                ),
            )

        actor_user = sorted(child_members, key=lambda entry: entry[1].id)[0][1]
        if payload.dry_run:
            return SystemPracticalTestOut(
                sent=True,
                dry_run=True,
                family_id=family_id,
                scenario="task_submitted",
                recipient_user_ids=recipient_user_ids,
                recipient_display_names=recipient_display_names,
                affected_entities={
                    "task_id": None,
                    "submission_id": None,
                    "actor_user_id": actor_user.id,
                    "actor_display_name": actor_user.display_name,
                    "created_at": now.isoformat(),
                },
                delivery_expectation="polling_based_local_notification",
            )

        task = create_test_task_for_user(
            assignee_user_id=actor_user.id,
            title=f"[Systemtest] Aufgabe eingereicht ({now.strftime('%Y-%m-%d %H:%M:%S')} UTC)",
            description=(
                "Praxis-Test für iOS-Benachrichtigungen. "
                "Diese Aufgabe wurde systemseitig erstellt und direkt eingereicht."
            ),
            due_at=now,
            reminder_offsets_minutes=[],
        )

        submission = TaskSubmission(
            task_id=task.id,
            submitted_by_id=actor_user.id,
            note="Systemtest: automatisch eingereicht",
        )
        db.add(submission)
        task.status = TaskStatusEnum.submitted
        db.flush()
        emit_live_event(
            db,
            family_id=family_id,
            event_type="task.submitted",
            payload={
                "task_id": task.id,
                "assignee_id": task.assignee_id,
                "source": "system_practical_test",
                "expected_recipient_user_ids": recipient_user_ids,
            },
        )
        db.commit()

        return SystemPracticalTestOut(
            sent=True,
            dry_run=False,
            family_id=family_id,
            scenario="task_submitted",
            recipient_user_ids=recipient_user_ids,
            recipient_display_names=recipient_display_names,
            affected_entities={
                "task_id": task.id,
                "submission_id": submission.id,
                "actor_user_id": actor_user.id,
                "actor_display_name": actor_user.display_name,
                "created_at": now.isoformat(),
            },
            delivery_expectation="polling_based_local_notification",
        )

    if payload.scenario in {"task_created", "task_due_reminder"}:
        invalid_roles = [
            user.display_name
            for membership, user in selected_recipients
            if membership.role != RoleEnum.child
        ]
        if invalid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Szenario '{payload.scenario}' richtet sich an Kinder. "
                    f"Ungültige Empfänger: {invalid_roles}"
                ),
            )

        due_at = None
        reminder_offsets: list[int] = []
        delivery_expectation = "polling_based_local_notification"
        if payload.scenario == "task_due_reminder":
            due_at = now + timedelta(minutes=1)
            reminder_offsets = [0]
            delivery_expectation = "reminder_scheduler_based_local_notification"

        if payload.dry_run:
            return SystemPracticalTestOut(
                sent=True,
                dry_run=True,
                family_id=family_id,
                scenario=payload.scenario,
                recipient_user_ids=recipient_user_ids,
                recipient_display_names=recipient_display_names,
                affected_entities={
                    "task_ids": [],
                    "submission_ids": [],
                    "created_at": now.isoformat(),
                    "reminder_notify_at": due_at.isoformat() if due_at else None,
                },
                delivery_expectation=delivery_expectation,
            )

        task_ids: list[int] = []
        for _, recipient_user in selected_recipients:
            title = "[Systemtest] Neue Aufgabe erstellt"
            description = "Praxis-Test für den normalen Aufgaben-Refresh in iOS."
            if payload.scenario == "task_due_reminder":
                title = "[Systemtest] Erinnerung zur Fälligkeit"
                description = (
                    "Praxis-Test für Erinnerungen zum Fälligkeitszeitpunkt. "
                    "Die Aufgabe ist in 1 Minute fällig."
                )
            task = create_test_task_for_user(
                assignee_user_id=recipient_user.id,
                title=f"{title} ({now.strftime('%Y-%m-%d %H:%M:%S')} UTC)",
                description=description,
                due_at=due_at,
                reminder_offsets_minutes=reminder_offsets,
            )
            task_ids.append(task.id)

        db.commit()
        return SystemPracticalTestOut(
            sent=True,
            dry_run=False,
            family_id=family_id,
            scenario=payload.scenario,
            recipient_user_ids=recipient_user_ids,
            recipient_display_names=recipient_display_names,
            affected_entities={
                "task_ids": task_ids,
                "submission_ids": [],
                "created_at": now.isoformat(),
                "reminder_notify_at": due_at.isoformat() if due_at else None,
            },
            delivery_expectation=delivery_expectation,
        )

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Szenario nicht unterstützt")
