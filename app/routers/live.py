import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import get_current_user_from_token_value
from ..models import LiveUpdateEvent, User
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


def _extract_bearer_token(authorization: str | None, access_token: str | None) -> str:
    token = (access_token or "").strip()
    if token:
        return token

    raw_authorization = (authorization or "").strip()
    if raw_authorization.lower().startswith("bearer "):
        bearer_token = raw_authorization[7:].strip()
        if bearer_token:
            return bearer_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token fehlt",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/families/{family_id}/live/stream")
async def stream_family_updates(
    family_id: int,
    request: Request,
    since_id: int = Query(default=0, ge=0),
    access_token: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
):
    token = _extract_bearer_token(authorization, access_token)
    current_user: User = get_current_user_from_token_value(token, db)
    get_membership_or_403(db, family_id, current_user.id)
    cursor = max(since_id, _parse_last_event_id(last_event_id))

    async def event_generator():
        nonlocal cursor
        connected_payload = {"family_id": family_id, "since_id": cursor}
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
            else:
                yield ": keep-alive\n\n"

            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
