from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..deps import get_current_user_from_token_value
from ..live_bus import live_event_bus
from ..models import (
    FamilyMembership,
    HomeAssistantSettings,
    LiveUpdateEvent,
    NotificationChannelEnum,
    RoleEnum,
    User,
)
from ..rbac import get_membership_or_403
from ..services import parse_live_payload

router = APIRouter(tags=["live"])
logger = logging.getLogger(__name__)


def _as_positive_int(value: object) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _event_payload_for_user(
    event_type: str,
    payload: dict,
    *,
    user_id: int,
    role: RoleEnum,
) -> dict | None:
    if role != RoleEnum.child:
        return payload

    if event_type.startswith("system.") or event_type.startswith("member.") or event_type == "event.created":
        return None

    if event_type == "notification.test":
        recipients = payload.get("recipient_user_ids")
        if isinstance(recipients, list):
            normalized = {_as_positive_int(entry) for entry in recipients}
            normalized.discard(None)
            if normalized and user_id not in normalized:
                return None
        return payload

    scoped_keys = ("assignee_id", "user_id", "requested_by_id", "contributor_user_id")
    for key in scoped_keys:
        target_user_id = _as_positive_int(payload.get(key))
        if target_user_id is not None:
            return payload if target_user_id == user_id else None

    if event_type.startswith("task."):
        # Löschereignisse älterer Clients enthalten nicht immer assignee_id. Kinder
        # erhalten dann nur einen Refresh-Hinweis, aber keine fremden Aufgabendaten.
        return {"task_id": payload.get("task_id"), "refresh_required": True}
    if event_type.startswith("achievement.") or event_type.startswith("points."):
        return None
    if event_type.startswith("reward."):
        return {"reward_id": payload.get("reward_id"), "refresh_required": True}
    if event_type.startswith("special_task_template."):
        return {"template_id": payload.get("template_id"), "refresh_required": True}
    return None


def _parse_last_event_id(last_event_id: str | None) -> int:
    if not last_event_id:
        return 0
    try:
        value = int(last_event_id)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def _extract_bearer_token(
    authorization: str | None,
    access_token: str | None,
    cookie_token: str | None,
) -> tuple[str, str, bool]:
    query_token = (access_token or "").strip()
    cookie_value = (cookie_token or "").strip()
    raw_authorization = (authorization or "").strip()
    if raw_authorization.lower().startswith("bearer "):
        header_token = raw_authorization[7:].strip()
        if header_token:
            token_conflict = bool(query_token and query_token != header_token)
            return header_token, "authorization_header", token_conflict

    if cookie_value:
        token_conflict = bool(query_token and query_token != cookie_value)
        return cookie_value, "auth_cookie", token_conflict

    if query_token:
        if not settings.sse_allow_query_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Query-Token ist deaktiviert. Bitte Authorization Header oder Cookie verwenden.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return query_token, "access_token_query", False

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token fehlt",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _active_notification_channel(family_id: int) -> str:
    with SessionLocal() as db:
        row = (
            db.query(HomeAssistantSettings.notification_channel)
            .filter(HomeAssistantSettings.family_id == family_id)
            .first()
        )
    raw = row[0] if row and row[0] else NotificationChannelEnum.sse.value
    try:
        return NotificationChannelEnum(str(raw)).value
    except ValueError:
        return NotificationChannelEnum.sse.value


def _stream_membership_active(db: Session, *, family_id: int, user_id: int) -> bool:
    return (
        db.query(FamilyMembership.id)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(
            FamilyMembership.family_id == family_id,
            FamilyMembership.user_id == user_id,
            User.is_active.is_(True),
        )
        .first()
        is not None
    )


@router.get("/families/{family_id}/live/stream")
async def stream_family_updates(
    family_id: int,
    request: Request,
    since_id: int = Query(default=0, ge=0),
    access_token: str | None = Query(default=None),
    fp_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    token, token_source, token_conflict = _extract_bearer_token(authorization, access_token, fp_token)
    if token_conflict:
        # Ambige Auth-Zustände (Header/Cookie/Query mit unterschiedlichen Tokens) strikt ablehnen.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Widersprüchliche Auth-Tokens übermittelt",
            headers={"WWW-Authenticate": "Bearer"},
        )
    with SessionLocal() as auth_db:
        current_user: User = get_current_user_from_token_value(token, auth_db)
        membership_context = get_membership_or_403(auth_db, family_id, current_user.id)
        current_user_id = int(current_user.id)
        current_user_role = membership_context.role
    cursor = max(since_id, _parse_last_event_id(last_event_id))
    active_channel = _active_notification_channel(family_id)

    async def event_generator():
        nonlocal cursor
        signal_version = live_event_bus.current_version(family_id)
        last_auth_check_at = time.monotonic()
        connected_payload = {
            "family_id": family_id,
            "since_id": cursor,
            "user_id": current_user_id,
            "auth_source": token_source,
            "token_conflict": token_conflict,
            "active_notification_channel": active_channel,
        }
        yield f"event: connected\ndata: {json.dumps(connected_payload, ensure_ascii=False)}\n\n"

        while True:
            if await request.is_disconnected():
                break

            with SessionLocal() as stream_db:
                try:
                    now_monotonic = time.monotonic()
                    if now_monotonic - last_auth_check_at >= 60.0:
                        if not _stream_membership_active(
                            stream_db,
                            family_id=family_id,
                            user_id=current_user_id,
                        ):
                            logger.info(
                                "Live-Stream wegen entzogenem Zugriff beendet "
                                "(family_id=%s, user_id=%s)",
                                family_id,
                                current_user_id,
                            )
                            break
                        last_auth_check_at = now_monotonic
                    events = (
                        stream_db.query(LiveUpdateEvent)
                        .filter(LiveUpdateEvent.family_id == family_id, LiveUpdateEvent.id > cursor)
                        .order_by(LiveUpdateEvent.id.asc())
                        .limit(200)
                        .all()
                    )
                except Exception:
                    logger.exception("Live-Stream DB-Abfrage fehlgeschlagen (family_id=%s)", family_id)
                    await asyncio.sleep(1.0)
                    continue

            if events:
                for event in events:
                    cursor = event.id
                    try:
                        parsed_payload = parse_live_payload(event.payload_json)
                    except Exception:
                        logger.exception(
                            "Live-Stream Payload parsing fehlgeschlagen (family_id=%s, event_id=%s)",
                            family_id,
                            event.id,
                        )
                        parsed_payload = {}
                    parsed_payload = _event_payload_for_user(
                        event.event_type,
                        parsed_payload,
                        user_id=current_user_id,
                        role=current_user_role,
                    )
                    if parsed_payload is None:
                        continue
                    payload = {
                        "id": event.id,
                        "family_id": event.family_id,
                        "event_type": event.event_type,
                        "payload": parsed_payload,
                        "created_at": event.created_at.isoformat(),
                    }
                    yield (
                        f"id: {event.id}\n"
                        "event: family_update\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                    if event.event_type == "notification.test":
                        direct_payload = {
                            "id": event.id,
                            "family_id": event.family_id,
                            "event_type": event.event_type,
                            "created_at": event.created_at.isoformat(),
                            "payload": parsed_payload,
                            "title": parsed_payload.get("title"),
                            "message": parsed_payload.get("message"),
                            "recipient_user_ids": parsed_payload.get("recipient_user_ids", []),
                        }
                        yield (
                            f"id: {event.id}\n"
                            "event: notification.test\n"
                            f"data: {json.dumps(direct_payload, ensure_ascii=False)}\n\n"
                        )
            else:
                yield ": keep-alive\n\n"

            signal_version = await live_event_bus.wait_for_update(
                family_id,
                signal_version,
                15.0,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
