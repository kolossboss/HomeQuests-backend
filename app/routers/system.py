import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..database import get_db
from ..deps import get_current_user
from ..models import FamilyMembership, HomeAssistantSettings, LiveUpdateEvent, NotificationChannelEnum, PushDevice, RecurrenceTypeEnum, RoleEnum, Task, TaskStatusEnum, TaskSubmission, User
from ..push_notifications import dispatch_home_assistant_notification, dispatch_remote_pushes_for_event
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    HomeAssistantUserConfigOut,
    HomeAssistantUserConfigUpdateRequest,
    HomeAssistantSettingsOut,
    HomeAssistantSettingsUpdateRequest,
    HomeAssistantUserTestRequest,
    NotificationChannelUpdateRequest,
    SystemEventOut,
    SystemPracticalTestOut,
    SystemPracticalTestRequest,
    SystemRuntimeOut,
    SystemTestNotificationOut,
    SystemTestNotificationRequest,
)
from ..secret_store import encrypt_secret
from ..services import emit_live_event

router = APIRouter(tags=["system"])


def _get_or_create_home_assistant_settings(db: Session, family_id: int) -> HomeAssistantSettings:
    settings = (
        db.query(HomeAssistantSettings)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    if settings:
        return settings

    settings = HomeAssistantSettings(
        family_id=family_id,
        ha_enabled=False,
        notification_channel=NotificationChannelEnum.sse.value,
        ha_base_url=None,
        ha_token=None,
        verify_ssl=True,
        updated_by_id=None,
    )
    db.add(settings)
    db.flush()
    return settings


def _serialize_home_assistant_user(member: FamilyMembership, user: User) -> HomeAssistantUserConfigOut:
    return HomeAssistantUserConfigOut(
        user_id=user.id,
        display_name=user.display_name,
        role=member.role,
        is_active=user.is_active,
        ha_notify_service=user.ha_notify_service,
        ha_notifications_enabled=bool(user.ha_notifications_enabled),
        ha_child_new_task=bool(user.ha_child_new_task),
        ha_manager_task_submitted=bool(user.ha_manager_task_submitted),
        ha_manager_reward_requested=bool(user.ha_manager_reward_requested),
        ha_task_due_reminder=bool(user.ha_task_due_reminder),
    )


def _apns_configured() -> bool:
    if not app_settings.apns_enabled:
        return False
    if not app_settings.apns_team_id or not app_settings.apns_key_id:
        return False
    if app_settings.apns_private_key and app_settings.apns_private_key.strip():
        return True
    if app_settings.apns_private_key_path and Path(app_settings.apns_private_key_path).exists():
        return True
    return False


def _decode_event_payload(raw_payload: str | None) -> dict[str, object] | None:
    if not raw_payload:
        return None
    try:
        parsed = json.loads(raw_payload)
    except Exception:
        return {"raw": raw_payload}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


@router.get("/families/{family_id}/system/runtime", response_model=SystemRuntimeOut)
def get_system_runtime(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})
    return SystemRuntimeOut(
        app_name=app_settings.app_name,
        app_version=app_settings.app_version,
        app_build_ref=app_settings.app_build_ref,
        server_time_utc=datetime.utcnow(),
    )


@router.get("/families/{family_id}/system/events", response_model=list[SystemEventOut])
def list_system_events(
    family_id: int,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    safe_limit = max(10, min(int(limit), 500))
    rows = (
        db.query(LiveUpdateEvent)
        .filter(LiveUpdateEvent.family_id == family_id)
        .order_by(LiveUpdateEvent.id.desc())
        .limit(safe_limit)
        .all()
    )
    return [
        SystemEventOut(
            id=entry.id,
            event_type=entry.event_type,
            payload=_decode_event_payload(entry.payload_json),
            created_at=entry.created_at,
        )
        for entry in rows
    ]


@router.get("/families/{family_id}/system/home-assistant-settings", response_model=HomeAssistantSettingsOut)
def get_home_assistant_settings(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    settings = (
        db.query(HomeAssistantSettings)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    if settings is None:
        return HomeAssistantSettingsOut(
            ha_enabled=False,
            notification_channel=NotificationChannelEnum.sse,
            ha_base_url=None,
            verify_ssl=True,
            has_token=False,
        )

    return HomeAssistantSettingsOut(
        ha_enabled=bool(settings.ha_enabled),
        notification_channel=NotificationChannelEnum(settings.notification_channel or NotificationChannelEnum.sse.value),
        ha_base_url=settings.ha_base_url,
        verify_ssl=bool(settings.verify_ssl),
        has_token=bool(settings.ha_token),
    )


@router.put("/families/{family_id}/system/home-assistant-settings", response_model=HomeAssistantSettingsOut)
def update_home_assistant_settings(
    family_id: int,
    payload: HomeAssistantSettingsUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    settings = _get_or_create_home_assistant_settings(db, family_id)
    if payload.notification_channel == NotificationChannelEnum.home_assistant:
        if not payload.ha_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Für den Kanal 'home_assistant' muss HA aktiviert sein",
            )
        has_existing_token = bool(settings.ha_token)
        if not payload.ha_base_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Für den Kanal 'home_assistant' ist eine Base URL erforderlich",
            )
        if payload.ha_token is None and payload.keep_existing_token and not has_existing_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Für den Kanal 'home_assistant' ist ein Token erforderlich",
            )

    settings.ha_enabled = payload.ha_enabled
    settings.notification_channel = payload.notification_channel.value
    settings.ha_base_url = payload.ha_base_url
    settings.verify_ssl = payload.verify_ssl
    settings.updated_by_id = current_user.id
    if payload.ha_token is not None:
        settings.ha_token = encrypt_secret(payload.ha_token)
    elif not payload.keep_existing_token:
        settings.ha_token = None

    db.commit()
    db.refresh(settings)
    return HomeAssistantSettingsOut(
        ha_enabled=bool(settings.ha_enabled),
        notification_channel=NotificationChannelEnum(settings.notification_channel or NotificationChannelEnum.sse.value),
        ha_base_url=settings.ha_base_url,
        verify_ssl=bool(settings.verify_ssl),
        has_token=bool(settings.ha_token),
    )


@router.get("/families/{family_id}/system/notification-channels-status")
def get_notification_channels_status(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    settings = (
        db.query(HomeAssistantSettings)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    active_channel = NotificationChannelEnum.sse
    if settings and settings.notification_channel:
        try:
            active_channel = NotificationChannelEnum(settings.notification_channel)
        except ValueError:
            active_channel = NotificationChannelEnum.sse

    apns_is_configured = _apns_configured()
    apns_device_count = (
        db.query(PushDevice.id)
        .filter(PushDevice.family_id == family_id, PushDevice.notifications_enabled == True)  # noqa: E712
        .count()
    )
    ha_user_count = (
        db.query(User.id)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(
            FamilyMembership.family_id == family_id,
            User.is_active == True,  # noqa: E712
            User.ha_notifications_enabled == True,  # noqa: E712
            User.ha_notify_service.is_not(None),
        )
        .count()
    )
    ha_has_url = bool(settings and settings.ha_base_url)
    ha_has_token = bool(settings and settings.ha_token)
    ha_enabled = bool(settings and settings.ha_enabled)
    ha_is_configured = ha_enabled and ha_has_url and ha_has_token and ha_user_count > 0

    return {
        "active_channel": active_channel.value,
        "channels": {
            "apns": {
                "active": active_channel == NotificationChannelEnum.apns,
                "configured": apns_is_configured,
                "device_count": apns_device_count,
                "status": (
                    f"APNs konfiguriert, {apns_device_count} Gerät(e) registriert"
                    if apns_is_configured
                    else "APNs nicht vollständig konfiguriert (ENV/Keys prüfen)"
                ),
            },
            "home_assistant": {
                "active": active_channel == NotificationChannelEnum.home_assistant,
                "configured": ha_is_configured,
                "ha_enabled": ha_enabled,
                "has_url": ha_has_url,
                "has_token": ha_has_token,
                "configured_user_count": ha_user_count,
                "status": (
                    f"HA bereit, {ha_user_count} Nutzer konfiguriert"
                    if ha_is_configured
                    else "HA nicht vollständig konfiguriert (URL/Token/Nutzer prüfen)"
                ),
            },
            "sse": {
                "active": active_channel == NotificationChannelEnum.sse,
                "configured": True,
                "status": "SSE-Live-Stream verfügbar",
            },
        },
    }


@router.put("/families/{family_id}/system/notification-channel")
def update_notification_channel(
    family_id: int,
    payload: NotificationChannelUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    settings = _get_or_create_home_assistant_settings(db, family_id)
    if payload.channel == NotificationChannelEnum.apns:
        if not _apns_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="APNs ist nicht vollständig konfiguriert (APNS_* Einstellungen prüfen)",
            )
    elif payload.channel == NotificationChannelEnum.home_assistant:
        ha_user_count = (
            db.query(User.id)
            .join(FamilyMembership, FamilyMembership.user_id == User.id)
            .filter(
                FamilyMembership.family_id == family_id,
                User.is_active == True,  # noqa: E712
                User.ha_notifications_enabled == True,  # noqa: E712
                User.ha_notify_service.is_not(None),
            )
            .count()
        )
        if not settings.ha_enabled or not settings.ha_base_url or not settings.ha_token or ha_user_count <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Home Assistant ist nicht vollständig konfiguriert (URL/Token/Nutzer prüfen)",
            )

    settings.notification_channel = payload.channel.value
    settings.updated_by_id = current_user.id
    db.commit()
    return {"updated": True, "active_channel": payload.channel.value}


@router.get("/families/{family_id}/system/home-assistant-users", response_model=list[HomeAssistantUserConfigOut])
def list_home_assistant_user_configs(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    rows = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id)
        .order_by(User.display_name.asc())
        .all()
    )
    return [_serialize_home_assistant_user(member, user) for member, user in rows]


@router.put("/families/{family_id}/system/home-assistant-users/{user_id}", response_model=HomeAssistantUserConfigOut)
def update_home_assistant_user_config(
    family_id: int,
    user_id: int,
    payload: HomeAssistantUserConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    row = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mitglied nicht gefunden")

    if payload.ha_notifications_enabled and not payload.ha_notify_service:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Für aktivierte HA-Benachrichtigungen ist ein Notify-Service erforderlich",
        )

    member, user = row
    user.ha_notify_service = payload.ha_notify_service
    user.ha_notifications_enabled = payload.ha_notifications_enabled
    user.ha_child_new_task = payload.ha_child_new_task
    user.ha_manager_task_submitted = payload.ha_manager_task_submitted
    user.ha_manager_reward_requested = payload.ha_manager_reward_requested
    user.ha_task_due_reminder = payload.ha_task_due_reminder

    db.commit()
    db.refresh(user)
    return _serialize_home_assistant_user(member, user)


@router.post("/families/{family_id}/system/home-assistant-users/{user_id}/test")
def send_home_assistant_user_test(
    family_id: int,
    user_id: int,
    payload: HomeAssistantUserTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    row = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id, User.is_active == True)  # noqa: E712
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mitglied nicht gefunden")

    _, user = row
    if not user.ha_notify_service:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="HA Notify-Service fehlt für diesen Nutzer")

    summary = dispatch_home_assistant_notification(
        db,
        family_id=family_id,
        title=payload.title,
        body=payload.message,
        recipient_user_ids=[user_id],
        event_type="notification.test.manual.user",
        dedupe_key=f"manual-ha-user-test:{family_id}:{user_id}:{datetime.utcnow().isoformat()}",
    )
    db.commit()
    return {"sent": summary.sent_count > 0, "delivery": summary.as_dict()}


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
    home_assistant_delivery = None
    sent = False
    settings = (
        db.query(HomeAssistantSettings)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    active_channel = NotificationChannelEnum.sse
    if settings and settings.notification_channel:
        try:
            active_channel = NotificationChannelEnum(settings.notification_channel)
        except ValueError:
            active_channel = NotificationChannelEnum.sse

    requested_channel = payload.test_channel
    if payload.send_via_home_assistant and requested_channel == "active":
        requested_channel = "home_assistant"
    if requested_channel == "active":
        effective_channel = active_channel
    else:
        effective_channel = NotificationChannelEnum(requested_channel)

    event = emit_live_event(
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
        dispatch_notifications=False,
    )

    if effective_channel == NotificationChannelEnum.apns:
        if not app_settings.apns_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="APNs ist nicht aktiviert oder konfiguriert",
            )
        dispatch_summary = dispatch_remote_pushes_for_event(
            db,
            family_id=family_id,
            event=event,
            payload={
                "title": payload.title,
                "message": payload.message,
                "recipient_user_ids": recipient_user_ids,
            },
            forced_channel=NotificationChannelEnum.apns,
        )
        sent = dispatch_summary.sent_count > 0
    elif effective_channel == NotificationChannelEnum.home_assistant:
        ha_summary = dispatch_home_assistant_notification(
            db,
            family_id=family_id,
            title=payload.title,
            body=payload.message,
            recipient_user_ids=recipient_user_ids,
            event_type="notification.test.manual",
            preference_key=None,
            dedupe_key=f"manual-test:{sent_at}",
        )
        home_assistant_delivery = ha_summary.as_dict()
        sent = ha_summary.sent_count > 0
    else:
        # SSE-Test gilt als gesendet, sobald Event erfolgreich persistiert wurde.
        sent = True

    db.commit()

    return SystemTestNotificationOut(
        sent=sent,
        family_id=family_id,
        title=payload.title,
        message=payload.message,
        recipient_count=len(recipient_user_ids),
        recipient_user_ids=recipient_user_ids,
        recipient_display_names=recipient_display_names,
        test_channel=requested_channel,
        delivery_mode=effective_channel.value,
        event_type="notification.test",
        sent_at=sent_at,
        home_assistant_delivery=home_assistant_delivery,
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
                delivery_expectation="remote_push_or_live_refresh",
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
            delivery_expectation="remote_push_or_live_refresh",
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
        delivery_expectation = "remote_push_or_live_refresh"
        if payload.scenario == "task_due_reminder":
            due_at = now + timedelta(minutes=16)
            reminder_offsets = [15]
            delivery_expectation = "remote_push_or_reminder_worker"

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
                    "Die Aufgabe ist in 16 Minuten fällig, mit 15 Minuten Vorwarnung."
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
