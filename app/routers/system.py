import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings as app_settings
from ..database import get_db
from ..db_tools import (
    DbToolsError,
    backup_allowed_dirs as db_backup_allowed_dirs,
    backup_default_dir as db_backup_default_dir,
    backup_supported as db_backup_supported,
    create_backup_directory as db_create_backup_directory,
    create_backup as db_create_backup,
    database_engine_name as db_database_engine_name,
    list_backup_directories as db_list_backup_directories,
    pg_dump_available as db_pg_dump_available,
    pg_restore_available as db_pg_restore_available,
    resolve_backup_file_path as db_resolve_backup_file_path,
)
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
    SystemDbDirectoryBrowseOut,
    SystemDbDirectoryCreateOut,
    SystemDbDirectoryCreateRequest,
    SystemDbDirectoryEntryOut,
    SystemDbAnalyzeOut,
    SystemDbBackupOut,
    SystemDbBackupRequest,
    SystemDbCleanupOut,
    SystemDbCleanupRequest,
    SystemDbDiagnosticsOut,
    SystemDbToolsStatusOut,
    SystemEventOut,
    SystemPracticalTestOut,
    SystemPracticalTestRequest,
    SystemRuntimeOut,
    SystemTestNotificationOut,
    SystemTestNotificationRequest,
)
from ..secret_store import encrypt_secret
from ..services import emit_live_event
from .tasks import _run_family_task_maintenance

router = APIRouter(tags=["system"])
RUNTIME_BUILD_REF = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


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


def _collect_db_diagnostics(db: Session, family_id: int) -> SystemDbDiagnosticsOut:
    if db_database_engine_name() != "postgresql":
        return SystemDbDiagnosticsOut(
            duplicate_series_groups=0,
            duplicate_series_rows=0,
            weekly_flexible_duplicate_groups=0,
            weekly_flexible_duplicate_rows=0,
            inactive_open_like_count=0,
            stale_none_without_due_open_count=0,
        )

    duplicate_series_groups = int(
        db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT series_id
                    FROM tasks
                    WHERE family_id = :family_id
                      AND is_active = true
                      AND recurrence_type <> 'none'
                      AND series_id IS NOT NULL
                      AND status IN ('open','rejected','submitted')
                    GROUP BY series_id
                    HAVING COUNT(*) > 1
                ) AS grouped
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    duplicate_series_rows = int(
        db.execute(
            text(
                """
                SELECT COALESCE(SUM(grouped.cnt), 0)
                FROM (
                    SELECT COUNT(*) AS cnt
                    FROM tasks
                    WHERE family_id = :family_id
                      AND is_active = true
                      AND recurrence_type <> 'none'
                      AND series_id IS NOT NULL
                      AND status IN ('open','rejected','submitted')
                    GROUP BY series_id
                    HAVING COUNT(*) > 1
                ) AS grouped
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    weekly_flexible_duplicate_groups = int(
        db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT assignee_id, lower(trim(title)) AS title_norm, COALESCE(special_template_id, 0) AS template_id, date_trunc('week', created_at)
                    FROM tasks
                    WHERE family_id = :family_id
                      AND is_active = true
                      AND recurrence_type = 'weekly'
                      AND due_at IS NULL
                      AND status IN ('open','rejected')
                    GROUP BY assignee_id, lower(trim(title)), COALESCE(special_template_id, 0), date_trunc('week', created_at)
                    HAVING COUNT(*) > 1
                ) AS grouped
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    weekly_flexible_duplicate_rows = int(
        db.execute(
            text(
                """
                SELECT COALESCE(SUM(grouped.cnt), 0)
                FROM (
                    SELECT COUNT(*) AS cnt
                    FROM tasks
                    WHERE family_id = :family_id
                      AND is_active = true
                      AND recurrence_type = 'weekly'
                      AND due_at IS NULL
                      AND status IN ('open','rejected')
                    GROUP BY assignee_id, lower(trim(title)), COALESCE(special_template_id, 0), date_trunc('week', created_at)
                    HAVING COUNT(*) > 1
                ) AS grouped
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    inactive_open_like_count = int(
        db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE family_id = :family_id
                  AND is_active = false
                  AND recurrence_type <> 'none'
                  AND status IN ('open','rejected','submitted')
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    stale_none_without_due_open_count = int(
        db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE family_id = :family_id
                  AND is_active = true
                  AND recurrence_type = 'none'
                  AND due_at IS NULL
                  AND status IN ('open','rejected')
                  AND created_at < (NOW() - INTERVAL '1 day')
                """
            ),
            {"family_id": family_id},
        ).scalar()
        or 0
    )
    return SystemDbDiagnosticsOut(
        duplicate_series_groups=duplicate_series_groups,
        duplicate_series_rows=duplicate_series_rows,
        weekly_flexible_duplicate_groups=weekly_flexible_duplicate_groups,
        weekly_flexible_duplicate_rows=weekly_flexible_duplicate_rows,
        inactive_open_like_count=inactive_open_like_count,
        stale_none_without_due_open_count=stale_none_without_due_open_count,
    )


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
        app_build_ref=app_settings.app_build_ref or RUNTIME_BUILD_REF,
        server_time_utc=datetime.utcnow(),
    )


@router.get("/families/{family_id}/system/db-tools/status", response_model=SystemDbToolsStatusOut)
def get_db_tools_status(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    engine_name = db_database_engine_name()
    diagnostics = _collect_db_diagnostics(db, family_id)
    default_dir = db_backup_default_dir()
    return SystemDbToolsStatusOut(
        database_engine=engine_name,
        backup_supported=db_backup_supported(),
        backup_command_available=db_pg_dump_available(),
        restore_command_available=db_pg_restore_available(),
        backup_allowed_dirs=[str(entry) for entry in db_backup_allowed_dirs()],
        backup_default_dir=str(default_dir),
        backup_timeout_seconds=app_settings.db_backup_timeout_seconds,
        cleanup_max_passes=app_settings.db_cleanup_max_passes,
        diagnostics=diagnostics,
        server_time_utc=datetime.utcnow(),
    )


def _serialize_db_directory_browse(path_or_none: str | None = None) -> SystemDbDirectoryBrowseOut:
    current, parent, directories = db_list_backup_directories(path_or_none)
    return SystemDbDirectoryBrowseOut(
        allowed_roots=[str(entry) for entry in db_backup_allowed_dirs()],
        current_path=str(current),
        parent_path=str(parent) if parent else None,
        directories=[
            SystemDbDirectoryEntryOut(name=entry.name, path=str(entry))
            for entry in directories
        ],
    )


@router.get("/families/{family_id}/system/db-tools/directories", response_model=SystemDbDirectoryBrowseOut)
def browse_db_backup_directories(
    family_id: int,
    path: str | None = Query(default=None, max_length=1024),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})
    try:
        return _serialize_db_directory_browse(path)
    except DbToolsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Verzeichnis konnte nicht geladen werden",
        ) from exc


@router.post(
    "/families/{family_id}/system/db-tools/directories/create",
    response_model=SystemDbDirectoryCreateOut,
)
def create_db_backup_directory(
    family_id: int,
    payload: SystemDbDirectoryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    try:
        created_path = db_create_backup_directory(
            parent_dir=payload.parent_dir,
            directory_name=payload.directory_name,
        )
        browse = _serialize_db_directory_browse(str(created_path))
    except DbToolsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Ordner konnte nicht erstellt werden",
        ) from exc

    emit_live_event(
        db,
        family_id=family_id,
        event_type="system.db.backup_directory_created",
        payload={
            "path": str(created_path),
            "parent_dir": payload.parent_dir,
            "created_by_id": current_user.id,
        },
    )
    db.commit()
    return SystemDbDirectoryCreateOut(
        created_path=str(created_path),
        browse=browse,
    )


@router.post("/families/{family_id}/system/db-tools/backup", response_model=SystemDbBackupOut)
def run_db_backup(
    family_id: int,
    payload: SystemDbBackupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    try:
        result = db_create_backup(
            target_dir=payload.target_dir,
            filename_prefix=payload.filename_prefix,
            timeout_seconds=app_settings.db_backup_timeout_seconds,
        )
    except DbToolsError as exc:
        detail = str(exc) or "Backup fehlgeschlagen"
        status_code = status.HTTP_400_BAD_REQUEST if (
            "Backup-Ziel" in detail
            or "Backup ist aktuell nur für PostgreSQL" in detail
            or "Ungültige DATABASE_URL" in detail
        ) else status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(status_code=status_code, detail=detail) from exc

    emit_live_event(
        db,
        family_id=family_id,
        event_type="system.db.backup_created",
        payload={
            "file_path": result.file_path,
            "file_size_bytes": result.file_size_bytes,
            "duration_seconds": result.duration_seconds,
            "created_by_id": current_user.id,
        },
    )
    db.commit()
    return SystemDbBackupOut(
        ok=True,
        file_path=result.file_path,
        file_size_bytes=result.file_size_bytes,
        duration_seconds=result.duration_seconds,
        created_at_utc=result.created_at_utc,
        database_engine=result.database_engine,
    )


@router.get("/families/{family_id}/system/db-tools/backup/download")
def download_db_backup_file(
    family_id: int,
    backup_file: str = Query(..., min_length=1, max_length=1024),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    try:
        resolved = db_resolve_backup_file_path(backup_file)
    except DbToolsError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc) or "Backup-Datei ungültig") from exc

    return FileResponse(
        path=str(resolved),
        media_type="application/octet-stream",
        filename=resolved.name,
    )


@router.post("/families/{family_id}/system/db-tools/cleanup", response_model=SystemDbCleanupOut)
def run_db_cleanup(
    family_id: int,
    payload: SystemDbCleanupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    requested_passes = int(payload.max_passes or app_settings.db_cleanup_max_passes)
    requested_passes = max(1, min(requested_passes, app_settings.db_cleanup_max_passes))
    started = datetime.utcnow()
    diagnostics_before = _collect_db_diagnostics(db, family_id)

    executed_passes = 0
    changed_passes = 0
    for _ in range(requested_passes):
        changed = _run_family_task_maintenance(db, family_id)
        executed_passes += 1
        if changed:
            changed_passes += 1
            db.commit()
            continue
        db.rollback()
        break

    diagnostics_after = _collect_db_diagnostics(db, family_id)
    finished = datetime.utcnow()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="system.db.cleanup_run",
        payload={
            "requested_max_passes": requested_passes,
            "executed_passes": executed_passes,
            "changed_passes": changed_passes,
            "created_by_id": current_user.id,
            "diagnostics_before": diagnostics_before.model_dump(),
            "diagnostics_after": diagnostics_after.model_dump(),
        },
    )
    db.commit()
    return SystemDbCleanupOut(
        ok=True,
        requested_max_passes=requested_passes,
        executed_passes=executed_passes,
        changed_passes=changed_passes,
        family_id=family_id,
        diagnostics_before=diagnostics_before,
        diagnostics_after=diagnostics_after,
        started_at_utc=started,
        finished_at_utc=finished,
    )


@router.post("/families/{family_id}/system/db-tools/analyze", response_model=SystemDbAnalyzeOut)
def run_db_analyze(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    started = datetime.utcnow()
    engine_name = db_database_engine_name()
    if engine_name == "postgresql":
        db.execute(text("ANALYZE"))
        db.commit()
    else:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"ANALYZE wird für DB-Engine '{engine_name}' derzeit nicht unterstützt",
        )
    finished = datetime.utcnow()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="system.db.analyze_run",
        payload={
            "created_by_id": current_user.id,
            "database_engine": engine_name,
            "started_at_utc": started.isoformat(),
            "finished_at_utc": finished.isoformat(),
        },
    )
    db.commit()
    return SystemDbAnalyzeOut(
        ok=True,
        database_engine=engine_name,
        started_at_utc=started,
        finished_at_utc=finished,
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
