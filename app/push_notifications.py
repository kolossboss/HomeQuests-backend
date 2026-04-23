from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import logging
import re
from threading import Lock
import time

import httpx
from jose import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    FamilyMembership,
    HomeAssistantDeliveryLog,
    HomeAssistantSettings,
    LiveUpdateEvent,
    NotificationChannelEnum,
    PushDeliveryLog,
    PushDevice,
    RoleEnum,
    Reward,
    Task,
    TaskStatusEnum,
    User,
)
from .secret_store import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)
_PROVIDER_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_PROVIDER_TOKEN_TTL_SECONDS = 45 * 60
_MAX_REMINDER_OFFSET_MINUTES = 2880
_PUSH_LOCK_KEY = 860032
_fallback_push_lock = Lock()


def _sanitize_error_reason(reason: str | None, *, max_len: int = 400) -> str | None:
    if reason is None:
        return None
    sanitized = str(reason).strip().replace("\n", " ")
    if not sanitized:
        return None
    sanitized = re.sub(r"(?i)bearer\s+[a-z0-9\-_=\.]+", "Bearer [REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(token=)[^\s&]+", r"\1[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)(authorization:)[^\s]+", r"\1[REDACTED]", sanitized)
    if len(sanitized) > max_len:
        return f"{sanitized[:max_len]}..."
    return sanitized


@dataclass
class PushPlan:
    title: str
    body: str
    recipient_user_ids: list[int]
    preference_key: str | None = None


@dataclass
class HomeAssistantDeliverySummary:
    sent_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failures: list[str] | None = None

    def add_failure(self, reason: str) -> None:
        if self.failures is None:
            self.failures = []
        self.failures.append(reason)
        self.failed_count += 1

    def as_dict(self) -> dict[str, object]:
        return {
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "failures": self.failures or [],
        }


@dataclass
class HomeAssistantRuntimeConfig:
    base_url: str
    token: str
    verify_ssl: bool


@dataclass
class NotificationDispatchSummary:
    channel: NotificationChannelEnum
    sent_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0


class APNsConfigurationError(RuntimeError):
    pass


class APNsClient:
    def __init__(self) -> None:
        self._private_key = self._load_private_key()

    def is_enabled(self) -> bool:
        return bool(
            settings.apns_enabled
            and settings.apns_team_id
            and settings.apns_key_id
            and self._private_key
        )

    def send_alert(
        self,
        *,
        device: PushDevice,
        title: str,
        body: str,
        event_type: str,
        family_id: int,
        event_id: int | None = None,
        dedupe_key: str | None = None,
    ) -> tuple[bool, str | None, str | None]:
        if not self.is_enabled():
            return False, None, "APNs nicht konfiguriert"

        apns_topic = settings.apns_bundle_id or device.bundle_id
        if not apns_topic:
            return False, None, "APNs Topic fehlt"

        provider_token = self._provider_token()
        host = "https://api.sandbox.push.apple.com" if device.push_environment == "development" else "https://api.push.apple.com"
        url = f"{host}/3/device/{device.device_token}"
        homequests_payload: dict[str, object] = {
            "family_id": family_id,
            "event_type": event_type,
        }
        if event_id is not None:
            homequests_payload["event_id"] = int(event_id)
        if dedupe_key:
            homequests_payload["dedupe_key"] = str(dedupe_key)

        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
            },
            "homequests": homequests_payload,
        }
        headers = {
            "authorization": f"bearer {provider_token}",
            "apns-topic": apns_topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if dedupe_key:
            collapse_id = re.sub(r"[^a-zA-Z0-9._-]", "-", str(dedupe_key))[:64]
            if collapse_id:
                headers["apns-collapse-id"] = collapse_id

        try:
            with httpx.Client(http2=True, timeout=10.0) as client:
                response = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            logger.exception("APNs-Versand fehlgeschlagen")
            return False, None, _sanitize_error_reason(str(exc))

        apns_id = response.headers.get("apns-id")
        if response.status_code == 200:
            return True, apns_id, None

        reason = None
        try:
            data = response.json()
            if isinstance(data, dict):
                reason = data.get("reason")
        except Exception:
            reason = response.text or None
        return False, apns_id, _sanitize_error_reason(reason or f"HTTP {response.status_code}")

    def _provider_token(self) -> str:
        cache_key = f"{settings.apns_team_id}:{settings.apns_key_id}"
        cached = _PROVIDER_TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and now - cached[1] < _PROVIDER_TOKEN_TTL_SECONDS:
            return cached[0]

        if not self._private_key or not settings.apns_team_id or not settings.apns_key_id:
            raise APNsConfigurationError("APNs-Credentials unvollständig")

        issued_at = int(now)
        token = jwt.encode(
            {"iss": settings.apns_team_id, "iat": issued_at},
            self._private_key,
            algorithm="ES256",
            headers={"alg": "ES256", "kid": settings.apns_key_id},
        )
        _PROVIDER_TOKEN_CACHE[cache_key] = (token, now)
        return token

    def _load_private_key(self) -> str | None:
        inline = (settings.apns_private_key or "").strip()
        if inline:
            return inline.replace("\\n", "\n")

        path = (settings.apns_private_key_path or "").strip()
        if not path:
            return None
        key_path = Path(path)
        if not key_path.exists():
            return None
        return key_path.read_text(encoding="utf-8")


_apns_client = APNsClient()


class HomeAssistantClient:
    def send_notify(
        self,
        *,
        base_url: str,
        token: str,
        verify_ssl: bool,
        notify_service: str,
        title: str,
        body: str,
        event_type: str,
        family_id: int,
    ) -> tuple[bool, str | None]:
        service = notify_service.strip()
        if not service:
            return False, "Kein HA Notify-Service konfiguriert"

        url_base = base_url.rstrip("/")
        if service == "persistent_notification":
            url = f"{url_base}/api/services/persistent_notification/create"
            payload = {"title": title, "message": body}
        else:
            url = f"{url_base}/api/services/notify/{service}"
            payload = {
                "title": title,
                "message": body,
                "data": {
                    "homequests": {
                        "family_id": family_id,
                        "event_type": event_type,
                    }
                },
            }
        headers = {"Authorization": f"Bearer {token}"}

        try:
            with httpx.Client(timeout=10.0, verify=verify_ssl) as client:
                response = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            logger.exception("Home Assistant Versand fehlgeschlagen")
            return False, _sanitize_error_reason(str(exc))

        if 200 <= response.status_code < 300:
            return True, None

        detail = _sanitize_error_reason(response.text.strip() or f"HTTP {response.status_code}")
        return False, detail


_ha_client = HomeAssistantClient()


def dispatch_remote_pushes_for_event(
    db: Session,
    *,
    family_id: int,
    event: LiveUpdateEvent,
    payload: dict | None = None,
    forced_channel: NotificationChannelEnum | None = None,
) -> NotificationDispatchSummary:
    plan = _build_push_plan(db, family_id=family_id, event_type=event.event_type, payload=payload or {})
    channel = forced_channel or _notification_channel_for_family(db, family_id)
    summary = NotificationDispatchSummary(channel=channel)
    if plan is None or not plan.recipient_user_ids:
        logger.info(
            "Push: kein Push-Plan fuer Event %s in Familie %s erzeugt",
            event.event_type,
            family_id,
        )
        return summary

    if channel == NotificationChannelEnum.sse:
        logger.info("Push: Kanal fuer Familie %s ist SSE, kein Remote-Versand fuer %s", family_id, event.event_type)
        summary.skipped_count = len(plan.recipient_user_ids)
        return summary

    if channel == NotificationChannelEnum.apns and settings.apns_enabled:
        devices = _eligible_devices(
            db,
            family_id=family_id,
            user_ids=plan.recipient_user_ids,
            preference_key=plan.preference_key,
        )
        if not devices:
            logger.warning(
                "APNs: keine passenden Geraete fuer Event %s in Familie %s gefunden (recipients=%s, preference=%s)",
                event.event_type,
                family_id,
                plan.recipient_user_ids,
                plan.preference_key,
            )
            summary.skipped_count += len(plan.recipient_user_ids)
        for device in devices:
            dedupe_key = f"live:{event.id}"
            if _delivery_exists(db, device.id, dedupe_key):
                logger.info(
                    "APNs: Event %s fuer device_id=%s bereits versendet (dedupe=%s)",
                    event.event_type,
                    device.id,
                    dedupe_key,
                )
                summary.skipped_count += 1
                continue
            sent, apns_id, reason = _apns_client.send_alert(
                device=device,
                title=plan.title,
                body=plan.body,
                event_type=event.event_type,
                family_id=family_id,
                event_id=int(event.id),
                dedupe_key=dedupe_key,
            )
            _record_delivery(
                db,
                device=device,
                family_id=family_id,
                user_id=device.user_id,
                dedupe_key=dedupe_key,
                event_type=event.event_type,
                sent=sent,
                apns_id=apns_id,
                reason=reason,
            )
            if sent:
                summary.sent_count += 1
                logger.info(
                    "APNs: Push fuer Event %s an user_id=%s device_id=%s erfolgreich gesendet (apns_id=%s)",
                    event.event_type,
                    device.user_id,
                    device.id,
                    apns_id,
                )
            else:
                summary.failed_count += 1
                logger.warning(
                    "APNs: Push fuer Event %s an user_id=%s device_id=%s fehlgeschlagen (%s)",
                    event.event_type,
                    device.user_id,
                    device.id,
                    _sanitize_error_reason(reason),
                )
            if reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
                db.delete(device)
    elif channel == NotificationChannelEnum.apns:
        logger.warning("APNs als aktiver Kanal gewählt, aber APNs ist nicht konfiguriert.")
        summary.failed_count += len(plan.recipient_user_ids)

    if channel == NotificationChannelEnum.home_assistant:
        ha_summary = dispatch_home_assistant_notification(
            db,
            family_id=family_id,
            title=plan.title,
            body=plan.body,
            recipient_user_ids=plan.recipient_user_ids,
            event_type=event.event_type,
            preference_key=plan.preference_key,
            dedupe_key=f"event:{event.id}",
        )
        summary.sent_count += ha_summary.sent_count
        summary.failed_count += ha_summary.failed_count
        summary.skipped_count += ha_summary.skipped_count
        if ha_summary.failed_count:
            logger.warning(
                "HA: %s Fehler bei Event %s (family=%s): %s",
                ha_summary.failed_count,
                event.event_type,
                family_id,
                ha_summary.failures or [],
            )
    return summary


def dispatch_home_assistant_notification(
    db: Session,
    *,
    family_id: int,
    title: str,
    body: str,
    recipient_user_ids: list[int],
    event_type: str,
    preference_key: str | None = None,
    dedupe_key: str,
) -> HomeAssistantDeliverySummary:
    summary = HomeAssistantDeliverySummary()
    config = _load_home_assistant_config(db, family_id)
    if config is None:
        summary.skipped_count = len(recipient_user_ids)
        return summary

    normalized_ids = sorted({int(entry) for entry in recipient_user_ids if int(entry) > 0})
    if not normalized_ids:
        return summary

    recipients = (
        db.query(User)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(
            FamilyMembership.family_id == family_id,
            User.id.in_(normalized_ids),
            User.is_active == True,  # noqa: E712
        )
        .all()
    )
    recipients_by_id = {int(entry.id): entry for entry in recipients}
    for user_id in normalized_ids:
        user = recipients_by_id.get(user_id)
        if user is None:
            summary.add_failure(f"user_id={user_id}: Benutzer nicht aktiv in Familie")
            continue
        if not _ha_user_allows_event(user, preference_key=preference_key):
            summary.skipped_count += 1
            continue
        notify_service = (user.ha_notify_service or "").strip()
        if not notify_service:
            summary.skipped_count += 1
            continue

        per_user_dedupe = f"{dedupe_key}:{user_id}:{notify_service}"
        if _ha_delivery_exists(
            db,
            family_id=family_id,
            user_id=user_id,
            notify_service=notify_service,
            dedupe_key=per_user_dedupe,
        ):
            summary.skipped_count += 1
            continue

        sent, reason = _ha_client.send_notify(
            base_url=config.base_url,
            token=config.token,
            verify_ssl=config.verify_ssl,
            notify_service=notify_service,
            title=title,
            body=body,
            event_type=event_type,
            family_id=family_id,
        )
        if sent:
            summary.sent_count += 1
            _record_ha_delivery(
                db,
                family_id=family_id,
                user_id=user_id,
                notify_service=notify_service,
                dedupe_key=per_user_dedupe,
                event_type=event_type,
                sent=True,
                reason=None,
            )
        else:
            sanitized_reason = _sanitize_error_reason(reason) or "unbekannter Fehler"
            summary.add_failure(f"user_id={user_id}: {sanitized_reason}")
            _record_ha_delivery(
                db,
                family_id=family_id,
                user_id=user_id,
                notify_service=notify_service,
                dedupe_key=per_user_dedupe,
                event_type=event_type,
                sent=False,
                reason=sanitized_reason,
            )
    return summary


def _load_home_assistant_config(db: Session, family_id: int) -> HomeAssistantRuntimeConfig | None:
    config = (
        db.query(HomeAssistantSettings)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    if config is None:
        return None
    if not config.ha_enabled:
        return None
    if not config.ha_base_url or not config.ha_token:
        return None
    token_value = str(config.ha_token)
    if not token_value.startswith("enc:v1:"):
        decrypted_token = token_value.strip()
        if decrypted_token:
            config.ha_token = encrypt_secret(decrypted_token)
            db.flush()
    else:
        decrypted_token = decrypt_secret(token_value)
    if not decrypted_token:
        return None
    return HomeAssistantRuntimeConfig(
        base_url=config.ha_base_url,
        token=decrypted_token,
        verify_ssl=bool(config.verify_ssl),
    )


def _notification_channel_for_family(db: Session, family_id: int) -> NotificationChannelEnum:
    row = (
        db.query(HomeAssistantSettings.notification_channel)
        .filter(HomeAssistantSettings.family_id == family_id)
        .first()
    )
    raw = (row[0] if row and row[0] else NotificationChannelEnum.sse.value)
    try:
        return NotificationChannelEnum(str(raw))
    except ValueError:
        return NotificationChannelEnum.sse


def _ha_user_allows_event(user: User, *, preference_key: str | None) -> bool:
    if not user.ha_notifications_enabled:
        return False
    if preference_key == "child_new_task":
        return bool(user.ha_child_new_task)
    if preference_key == "manager_task_submitted":
        return bool(user.ha_manager_task_submitted)
    if preference_key == "manager_reward_requested":
        return bool(user.ha_manager_reward_requested)
    if preference_key == "task_due_reminder":
        return bool(user.ha_task_due_reminder)
    return True


def has_any_enabled_home_assistant_config(db: Session) -> bool:
    row = (
        db.query(HomeAssistantSettings.id)
        .filter(
            HomeAssistantSettings.ha_enabled == True,  # noqa: E712
            HomeAssistantSettings.notification_channel == NotificationChannelEnum.home_assistant.value,
            HomeAssistantSettings.ha_base_url.is_not(None),
            HomeAssistantSettings.ha_token.is_not(None),
        )
        .first()
    )
    return row is not None


def _ha_delivery_exists(
    db: Session,
    *,
    family_id: int,
    user_id: int,
    notify_service: str,
    dedupe_key: str,
) -> bool:
    return (
        db.query(HomeAssistantDeliveryLog.id)
        .filter(
            HomeAssistantDeliveryLog.family_id == family_id,
            HomeAssistantDeliveryLog.user_id == user_id,
            HomeAssistantDeliveryLog.notify_service == notify_service,
            HomeAssistantDeliveryLog.dedupe_key == dedupe_key,
            HomeAssistantDeliveryLog.status == "sent",
        )
        .first()
        is not None
    )


def _record_ha_delivery(
    db: Session,
    *,
    family_id: int,
    user_id: int,
    notify_service: str,
    dedupe_key: str,
    event_type: str,
    sent: bool,
    reason: str | None,
) -> None:
    sanitized_reason = _sanitize_error_reason(reason)
    params = {
        "family_id": family_id,
        "user_id": user_id,
        "notify_service": notify_service,
        "dedupe_key": dedupe_key,
        "event_type": event_type,
        "status": "sent" if sent else "failed",
        "error_reason": sanitized_reason,
    }
    db.execute(
        text(
            "INSERT INTO home_assistant_delivery_logs "
            "(family_id, user_id, notify_service, dedupe_key, event_type, status, error_reason) "
            "VALUES (:family_id, :user_id, :notify_service, :dedupe_key, :event_type, :status, :error_reason) "
            "ON CONFLICT (family_id, user_id, notify_service, dedupe_key) DO UPDATE SET "
            "status = EXCLUDED.status, "
            "event_type = EXCLUDED.event_type, "
            "error_reason = EXCLUDED.error_reason, "
            "sent_at = CURRENT_TIMESTAMP"
        ),
        params,
    )


def _acquire_push_lock(db: Session) -> bool:
    if engine.dialect.name == "postgresql":
        return bool(db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _PUSH_LOCK_KEY}).scalar())
    return _fallback_push_lock.acquire(blocking=False)


def _release_push_lock(db: Session) -> None:
    if engine.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _PUSH_LOCK_KEY})
        return
    if _fallback_push_lock.locked():
        _fallback_push_lock.release()


def run_push_reminder_sweep_once() -> bool:
    now = datetime.utcnow()
    with SessionLocal() as db:  # type: ignore[name-defined]
        if not _acquire_push_lock(db):
            return False

        try:
            if not settings.apns_enabled and not has_any_enabled_home_assistant_config(db):
                logger.info("Push: Reminder-Worker uebersprungen, weder APNs noch Home Assistant aktiv")
                return False

            window_seconds = max(settings.push_worker_interval_seconds + 30, 90)
            earliest_due = now - timedelta(seconds=window_seconds)
            latest_due = now + timedelta(minutes=_MAX_REMINDER_OFFSET_MINUTES)
            tasks = (
                db.query(Task)
                .filter(
                    Task.is_active == True,  # noqa: E712
                    Task.status == TaskStatusEnum.open,
                    Task.due_at.is_not(None),
                    Task.due_at >= earliest_due,
                    Task.due_at <= latest_due,
                )
                .all()
            )
            devices_by_user: dict[tuple[int, int], list[PushDevice]] = {}
            if settings.apns_enabled:
                all_devices = (
                    db.query(PushDevice)
                    .filter(
                        PushDevice.notifications_enabled == True,  # noqa: E712
                        PushDevice.task_due_reminder == True,  # noqa: E712
                    )
                    .order_by(PushDevice.id.asc())
                    .all()
                )
                for device in all_devices:
                    key = (int(device.family_id), int(device.user_id))
                    devices_by_user.setdefault(key, []).append(device)

            changed = False
            had_any_delivery_attempt = False
            ha_sent_in_run: set[str] = set()
            channel_cache: dict[int, NotificationChannelEnum] = {}
            for task in tasks:
                due_at = task.due_at
                if due_at is None:
                    continue
                family_channel = channel_cache.get(int(task.family_id))
                if family_channel is None:
                    family_channel = _notification_channel_for_family(db, int(task.family_id))
                    channel_cache[int(task.family_id)] = family_channel
                if family_channel == NotificationChannelEnum.sse:
                    continue
                for offset in task.reminder_offsets_minutes or []:
                    notify_at = due_at - timedelta(minutes=int(offset))
                    if notify_at > now or (now - notify_at).total_seconds() > window_seconds:
                        continue
                    dedupe_key = f"reminder:{task.id}:{offset}:{due_at.isoformat()}"
                    if family_channel == NotificationChannelEnum.apns and settings.apns_enabled:
                        devices = devices_by_user.get((int(task.family_id), int(task.assignee_id)), [])
                        for device in devices:
                            if _delivery_exists(db, device.id, dedupe_key):
                                continue
                            had_any_delivery_attempt = True
                            sent, apns_id, reason = _apns_client.send_alert(
                                device=device,
                                title="Aufgaben-Erinnerung",
                                body=f"„{task.title}“ ist fällig: {due_at.strftime('%d.%m.%Y %H:%M')}",
                                event_type="task.due_reminder",
                                family_id=task.family_id,
                                dedupe_key=dedupe_key,
                            )
                            _record_delivery(
                                db,
                                device=device,
                                family_id=task.family_id,
                                user_id=device.user_id,
                                dedupe_key=dedupe_key,
                                event_type="task.due_reminder",
                                sent=sent,
                                apns_id=apns_id,
                                reason=reason,
                            )
                            if sent:
                                logger.info(
                                    "APNs: Reminder fuer task_id=%s an user_id=%s device_id=%s erfolgreich gesendet (apns_id=%s)",
                                    task.id,
                                    device.user_id,
                                    device.id,
                                    apns_id,
                                )
                            else:
                                logger.warning(
                                    "APNs: Reminder fuer task_id=%s an user_id=%s device_id=%s fehlgeschlagen (%s)",
                                    task.id,
                                    device.user_id,
                                    device.id,
                                    _sanitize_error_reason(reason),
                                )
                            if reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
                                db.delete(device)
                            changed = True

                    if family_channel == NotificationChannelEnum.home_assistant:
                        ha_run_key = f"{task.family_id}:{task.assignee_id}:{dedupe_key}"
                        if ha_run_key in ha_sent_in_run:
                            continue
                        ha_sent_in_run.add(ha_run_key)
                        ha_summary = dispatch_home_assistant_notification(
                            db,
                            family_id=task.family_id,
                            title="Aufgaben-Erinnerung",
                            body=f"„{task.title}“ ist fällig: {due_at.strftime('%d.%m.%Y %H:%M')}",
                            recipient_user_ids=[task.assignee_id],
                            event_type="task.due_reminder",
                            preference_key="task_due_reminder",
                            dedupe_key=dedupe_key,
                        )
                        if ha_summary.sent_count or ha_summary.failed_count:
                            had_any_delivery_attempt = True
                            changed = True
                        if ha_summary.failed_count:
                            logger.warning(
                                "HA: Reminder fuer task_id=%s an user_id=%s teilweise/komplett fehlgeschlagen: %s",
                                task.id,
                                task.assignee_id,
                                ha_summary.failures or [],
                            )
            if changed:
                db.commit()
            else:
                db.rollback()
            return had_any_delivery_attempt
        finally:
            _release_push_lock(db)


def _eligible_devices(
    db: Session,
    *,
    family_id: int,
    user_ids: list[int],
    preference_key: str | None,
) -> list[PushDevice]:
    if not user_ids:
        return []
    base_query = db.query(PushDevice).filter(
        PushDevice.family_id == family_id,
        PushDevice.user_id.in_(user_ids),
        PushDevice.notifications_enabled == True,  # noqa: E712
    )
    if preference_key == "child_new_task":
        base_query = base_query.filter(PushDevice.child_new_task == True)  # noqa: E712
    elif preference_key == "manager_task_submitted":
        base_query = base_query.filter(PushDevice.manager_task_submitted == True)  # noqa: E712
    elif preference_key == "manager_reward_requested":
        base_query = base_query.filter(PushDevice.manager_reward_requested == True)  # noqa: E712
    elif preference_key == "task_due_reminder":
        base_query = base_query.filter(PushDevice.task_due_reminder == True)  # noqa: E712

    fresh_cutoff = datetime.utcnow() - timedelta(days=45)
    fresh_devices = (
        base_query
        .filter(PushDevice.last_seen_at >= fresh_cutoff)
        .order_by(PushDevice.last_seen_at.desc(), PushDevice.id.desc())
        .all()
    )
    if fresh_devices:
        return fresh_devices
    return base_query.order_by(PushDevice.last_seen_at.desc(), PushDevice.id.desc()).all()


def _delivery_exists(db: Session, device_id: int, dedupe_key: str) -> bool:
    return (
        db.query(PushDeliveryLog.id)
        .filter(PushDeliveryLog.device_id == device_id, PushDeliveryLog.dedupe_key == dedupe_key)
        .first()
        is not None
    )


def _record_delivery(
    db: Session,
    *,
    device: PushDevice,
    family_id: int,
    user_id: int,
    dedupe_key: str,
    event_type: str,
    sent: bool,
    apns_id: str | None,
    reason: str | None,
) -> None:
    sanitized_reason = _sanitize_error_reason(reason)
    params = {
        "device_id": device.id,
        "family_id": family_id,
        "user_id": user_id,
        "dedupe_key": dedupe_key,
        "event_type": event_type,
        "apns_id": apns_id,
        "status": "sent" if sent else "failed",
        "error_reason": sanitized_reason,
    }
    db.execute(
        text(
            "INSERT INTO push_delivery_logs "
            "(device_id, family_id, user_id, dedupe_key, event_type, apns_id, status, error_reason) "
            "VALUES (:device_id, :family_id, :user_id, :dedupe_key, :event_type, :apns_id, :status, :error_reason) "
            "ON CONFLICT (device_id, dedupe_key) DO NOTHING"
        ),
        params,
    )


def _build_push_plan(db: Session, *, family_id: int, event_type: str, payload: dict) -> PushPlan | None:
    if event_type == "notification.test":
        recipient_ids = _normalize_user_ids(payload.get("recipient_user_ids")) or _active_member_user_ids(db, family_id)
        return PushPlan(
            title=(payload.get("title") or "Test-Benachrichtigung").strip(),
            body=(payload.get("message") or "Neue Mitteilung aus der Familie.").strip(),
            recipient_user_ids=recipient_ids,
        )

    if event_type == "task.created":
        task = _load_task(db, payload.get("task_id"))
        if task is None:
            return None
        return PushPlan(
            title="Neue Aufgabe",
            body=f"Du hast eine neue Aufgabe: {task.title}",
            recipient_user_ids=[task.assignee_id],
            preference_key="child_new_task",
        )

    if event_type in {"task.submitted", "task.missed_reported"}:
        task = _load_task(db, payload.get("task_id"))
        if task is None:
            return None
        actor = db.query(User).filter(User.id == task.assignee_id).first()
        actor_name = actor.display_name if actor else "Ein Kind"
        title = "Aufgabe erledigt gemeldet" if event_type == "task.submitted" else "Aufgabe als nicht erledigt gemeldet"
        body = (
            f"{actor_name} hat „{task.title}“ eingereicht."
            if event_type == "task.submitted"
            else f"{actor_name} konnte „{task.title}“ nicht erledigen."
        )
        return PushPlan(
            title=title,
            body=body,
            recipient_user_ids=_manager_user_ids(db, family_id),
            preference_key="manager_task_submitted",
        )

    if event_type == "reward.redeem_requested":
        reward = db.query(Reward).filter(Reward.id == payload.get("reward_id")).first()
        requester = db.query(User).filter(User.id == payload.get("requested_by_id")).first()
        if reward is None:
            return None
        requester_name = requester.display_name if requester else "Ein Kind"
        return PushPlan(
            title="Belohnung angefragt",
            body=f"{requester_name} hat „{reward.title}“ angefragt.",
            recipient_user_ids=_manager_user_ids(db, family_id),
            preference_key="manager_reward_requested",
        )

    if event_type == "achievement.unlocked":
        user_id = payload.get("user_id")
        try:
            recipient_id = int(user_id)
        except (TypeError, ValueError):
            return None
        achievement_name = str(payload.get("name") or "Neue Errungenschaft").strip()
        difficulty = _achievement_difficulty_label(str(payload.get("difficulty") or "").strip())
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        reward_points = int(reward.get("points") or 0)
        reward_suffix = f" Öffne dein Geschenk für {reward_points} Punkte." if reward_points > 0 else ""
        return PushPlan(
            title="Errungenschaft freigeschaltet!",
            body=f"{achievement_name} ({difficulty}) ist bereit für dein Profil.{reward_suffix}",
            recipient_user_ids=[recipient_id],
            preference_key=None,
        )

    return None


def _achievement_difficulty_label(value: str) -> str:
    labels = {
        "bronze": "Bronze",
        "silver": "Silber",
        "gold": "Gold",
        "platinum": "Platin",
    }
    return labels.get(value.lower(), value.capitalize() or "Achievement")


def _manager_user_ids(db: Session, family_id: int) -> list[int]:
    rows = (
        db.query(FamilyMembership.user_id)
        .filter(
            FamilyMembership.family_id == family_id,
            FamilyMembership.role.in_([RoleEnum.admin, RoleEnum.parent]),
        )
        .all()
    )
    return [int(row[0]) for row in rows]


def _active_member_user_ids(db: Session, family_id: int) -> list[int]:
    rows = (
        db.query(User.id)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(FamilyMembership.family_id == family_id, User.is_active == True)  # noqa: E712
        .all()
    )
    return [int(row[0]) for row in rows]


def _load_task(db: Session, task_id: object) -> Task | None:
    try:
        numeric_task_id = int(task_id)
    except (TypeError, ValueError):
        return None
    return db.query(Task).filter(Task.id == numeric_task_id).first()


def _normalize_user_ids(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    normalized: list[int] = []
    for entry in raw:
        try:
            normalized.append(int(entry))
        except (TypeError, ValueError):
            continue
    return normalized


from .database import SessionLocal, engine  # noqa: E402  # circular import safe here
