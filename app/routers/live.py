import asyncio
import json

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..config import settings
from ..database import SessionLocal
from ..deps import get_current_user_from_token_value
from ..live_bus import live_event_bus
from ..models import HomeAssistantSettings, LiveUpdateEvent, NotificationChannelEnum, User
from ..rbac import get_membership_or_403
from ..services import parse_live_payload

router = APIRouter(tags=["live"])


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
    with SessionLocal() as auth_db:
        current_user: User = get_current_user_from_token_value(token, auth_db)
        get_membership_or_403(auth_db, family_id, current_user.id)
    cursor = max(since_id, _parse_last_event_id(last_event_id))
    active_channel = _active_notification_channel(family_id)

    async def event_generator():
        nonlocal cursor
        signal_version = live_event_bus.current_version(family_id)
        connected_payload = {
            "family_id": family_id,
            "since_id": cursor,
            "user_id": current_user.id,
            "auth_source": token_source,
            "token_conflict": token_conflict,
            "active_notification_channel": active_channel,
        }
        yield f"event: connected\ndata: {json.dumps(connected_payload, ensure_ascii=False)}\n\n"

        while True:
            if await request.is_disconnected():
                break

            with SessionLocal() as stream_db:
                events = (
                    stream_db.query(LiveUpdateEvent)
                    .filter(LiveUpdateEvent.family_id == family_id, LiveUpdateEvent.id > cursor)
                    .order_by(LiveUpdateEvent.id.asc())
                    .limit(200)
                    .all()
                )

            if events:
                for event in events:
                    cursor = event.id
                    parsed_payload = parse_live_payload(event.payload_json)
                    if event.event_type == "notification.test":
                        recipient_user_ids = parsed_payload.get("recipient_user_ids")
                        if isinstance(recipient_user_ids, list):
                            normalized_recipient_ids = {
                                int(entry) for entry in recipient_user_ids if isinstance(entry, int) or str(entry).isdigit()
                            }
                            if normalized_recipient_ids and current_user.id not in normalized_recipient_ids:
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

            signal_version = await asyncio.to_thread(
                live_event_bus.wait_for_update,
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
