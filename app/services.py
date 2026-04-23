from __future__ import annotations

import json
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from .live_bus import live_event_bus
from .models import LiveUpdateEvent, PointsLedger
from .notification_dispatcher import enqueue_remote_dispatch_job

MAX_LIVE_EVENTS_PER_FAMILY = 5000
LIVE_EVENT_TRIM_BATCH_SIZE = 500
logger = logging.getLogger(__name__)


def get_points_balance(db: Session, family_id: int, user_id: int) -> int:
    result = (
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .scalar()
    )
    return int(result or 0)


def emit_live_event(
    db: Session,
    family_id: int,
    event_type: str,
    payload: dict | None = None,
    *,
    dispatch_notifications: bool = True,
) -> LiveUpdateEvent:
    event = LiveUpdateEvent(
        family_id=family_id,
        event_type=event_type,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
    )
    db.add(event)
    db.flush()
    if dispatch_notifications:
        queued = enqueue_remote_dispatch_job(
            family_id=family_id,
            event_id=int(event.id),
            payload=payload,
        )
        if not queued:
            # Fallback: Bei voller Queue weiterhin inline versenden, um Events nicht zu verlieren.
            try:
                from .push_notifications import dispatch_remote_pushes_for_event

                dispatch_remote_pushes_for_event(
                    db,
                    family_id=family_id,
                    event=event,
                    payload=payload,
                )
            except Exception:
                logger.exception("Remote-Push-Versand fehlgeschlagen")
    live_event_bus.publish(family_id)
    _trim_live_events(db, family_id)
    return event


def _trim_live_events(db: Session, family_id: int) -> None:
    stale_rows = (
        db.query(LiveUpdateEvent.id)
        .filter(LiveUpdateEvent.family_id == family_id)
        .order_by(LiveUpdateEvent.id.desc())
        .offset(MAX_LIVE_EVENTS_PER_FAMILY)
        .limit(LIVE_EVENT_TRIM_BATCH_SIZE)
        .all()
    )
    if not stale_rows:
        return

    stale_ids = [int(row[0]) for row in stale_rows]
    db.query(LiveUpdateEvent).filter(LiveUpdateEvent.id.in_(stale_ids)).delete(synchronize_session=False)


def parse_live_payload(payload_json: str | None) -> dict:
    if not payload_json:
        return {}
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
