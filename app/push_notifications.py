from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import logging
import time

import httpx
from jose import jwt
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    FamilyMembership,
    LiveUpdateEvent,
    PushDeliveryLog,
    PushDevice,
    RoleEnum,
    Reward,
    Task,
    TaskStatusEnum,
    User,
)

logger = logging.getLogger(__name__)
_PROVIDER_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_PROVIDER_TOKEN_TTL_SECONDS = 45 * 60


@dataclass
class PushPlan:
    title: str
    body: str
    recipient_user_ids: list[int]
    preference_key: str | None = None


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
    ) -> tuple[bool, str | None, str | None]:
        if not self.is_enabled():
            return False, None, "APNs nicht konfiguriert"

        apns_topic = settings.apns_bundle_id or device.bundle_id
        if not apns_topic:
            return False, None, "APNs Topic fehlt"

        provider_token = self._provider_token()
        host = "https://api.sandbox.push.apple.com" if device.push_environment == "development" else "https://api.push.apple.com"
        url = f"{host}/3/device/{device.device_token}"
        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
            },
            "homequests": {
                "family_id": family_id,
                "event_type": event_type,
            },
        }
        headers = {
            "authorization": f"bearer {provider_token}",
            "apns-topic": apns_topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }

        try:
            with httpx.Client(http2=True, timeout=10.0) as client:
                response = client.post(url, headers=headers, json=payload)
        except Exception as exc:
            logger.exception("APNs-Versand fehlgeschlagen")
            return False, None, str(exc)

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
        return False, apns_id, reason or f"HTTP {response.status_code}"

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


def dispatch_remote_pushes_for_event(
    db: Session,
    *,
    family_id: int,
    event: LiveUpdateEvent,
    payload: dict | None = None,
) -> None:
    plan = _build_push_plan(db, family_id=family_id, event_type=event.event_type, payload=payload or {})
    if plan is None or not plan.recipient_user_ids:
        return

    devices = _eligible_devices(
        db,
        family_id=family_id,
        user_ids=plan.recipient_user_ids,
        preference_key=plan.preference_key,
    )
    for device in devices:
        dedupe_key = f"live:{event.id}"
        if _delivery_exists(db, device.id, dedupe_key):
            continue
        sent, apns_id, reason = _apns_client.send_alert(
            device=device,
            title=plan.title,
            body=plan.body,
            event_type=event.event_type,
            family_id=family_id,
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
        if reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
            db.delete(device)


def run_push_reminder_sweep_once() -> bool:
    if not settings.apns_enabled:
        return False

    now = datetime.utcnow()
    with SessionLocal() as db:  # type: ignore[name-defined]
        candidates = (
            db.query(Task, PushDevice)
            .join(PushDevice, PushDevice.user_id == Task.assignee_id)
            .filter(
                PushDevice.notifications_enabled == True,  # noqa: E712
                PushDevice.task_due_reminder == True,  # noqa: E712
                Task.is_active == True,  # noqa: E712
                Task.status == TaskStatusEnum.open,
                Task.due_at.is_not(None),
            )
            .all()
        )
        changed = False
        window_seconds = max(settings.push_worker_interval_seconds + 30, 90)
        for task, device in candidates:
            if task.family_id != device.family_id:
                continue
            due_at = task.due_at
            if due_at is None:
                continue
            for offset in task.reminder_offsets_minutes or []:
                notify_at = due_at - timedelta(minutes=int(offset))
                if notify_at > now or (now - notify_at).total_seconds() > window_seconds:
                    continue
                dedupe_key = f"reminder:{task.id}:{offset}:{due_at.isoformat()}"
                if _delivery_exists(db, device.id, dedupe_key):
                    continue
                sent, apns_id, reason = _apns_client.send_alert(
                    device=device,
                    title="Aufgaben-Erinnerung",
                    body=f"„{task.title}“ ist fällig: {due_at.strftime('%d.%m.%Y %H:%M')}",
                    event_type="task.due_reminder",
                    family_id=task.family_id,
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
                if reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
                    db.delete(device)
                changed = True
        if changed:
            db.commit()
        else:
            db.rollback()
        return changed


def _eligible_devices(
    db: Session,
    *,
    family_id: int,
    user_ids: list[int],
    preference_key: str | None,
) -> list[PushDevice]:
    if not user_ids:
        return []
    query = db.query(PushDevice).filter(
        PushDevice.family_id == family_id,
        PushDevice.user_id.in_(user_ids),
        PushDevice.notifications_enabled == True,  # noqa: E712
    )
    if preference_key == "child_new_task":
        query = query.filter(PushDevice.child_new_task == True)  # noqa: E712
    elif preference_key == "manager_task_submitted":
        query = query.filter(PushDevice.manager_task_submitted == True)  # noqa: E712
    elif preference_key == "manager_reward_requested":
        query = query.filter(PushDevice.manager_reward_requested == True)  # noqa: E712
    elif preference_key == "task_due_reminder":
        query = query.filter(PushDevice.task_due_reminder == True)  # noqa: E712
    return query.order_by(PushDevice.id.asc()).all()


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
    db.add(
        PushDeliveryLog(
            device_id=device.id,
            family_id=family_id,
            user_id=user_id,
            dedupe_key=dedupe_key,
            event_type=event_type,
            apns_id=apns_id,
            status="sent" if sent else "failed",
            error_reason=reason,
        )
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

    return None


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


from .database import SessionLocal  # noqa: E402  # circular import safe here
