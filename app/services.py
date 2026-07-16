from __future__ import annotations

import json
import logging

from sqlalchemy import event, func
from sqlalchemy.orm import Session

from .live_bus import live_event_bus
from .models import LiveUpdateEvent, PointsLedger
from .notification_dispatcher import enqueue_remote_dispatch_job

MAX_LIVE_EVENTS_PER_FAMILY = 5000
LIVE_EVENT_TRIM_BATCH_SIZE = 500
logger = logging.getLogger(__name__)
_PENDING_LIVE_EVENTS_KEY = "homequests_pending_live_events"


def _publish_committed_events(session: Session) -> None:
    pending = session.info.pop(_PENDING_LIVE_EVENTS_KEY, [])
    for family_id, event_id, payload, dispatch_notifications in pending:
        try:
            live_event_bus.publish(family_id)
            if dispatch_notifications:
                queued = enqueue_remote_dispatch_job(
                    family_id=family_id,
                    event_id=event_id,
                    payload=payload,
                )
                if not queued:
                    logger.error(
                        "Remote-Push konnte nach Commit nicht eingeplant werden "
                        "(family_id=%s, event_id=%s)",
                        family_id,
                        event_id,
                    )
        except Exception:
            # Ein bereits erfolgreicher Fach-Commit darf nicht nachträglich als
            # API-Fehler erscheinen, nur weil ein optionaler Live-Kanal ausfällt.
            logger.exception(
                "Live-/Push-Signal nach Commit fehlgeschlagen (family_id=%s, event_id=%s)",
                family_id,
                event_id,
            )


@event.listens_for(Session, "after_commit")
def _after_session_commit(session: Session) -> None:
    _publish_committed_events(session)


@event.listens_for(Session, "after_rollback")
def _after_session_rollback(session: Session) -> None:
    session.info.pop(_PENDING_LIVE_EVENTS_KEY, None)


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
    db.info.setdefault(_PENDING_LIVE_EVENTS_KEY, []).append(
        (int(family_id), int(event.id), payload, bool(dispatch_notifications))
    )
    _trim_live_events(db, family_id)
    return event


def _trim_live_events(db: Session, family_id: int) -> None:
    stale_rows = (
        db.query(LiveUpdateEvent.id)
        .filter(LiveUpdateEvent.family_id == family_id)
        .order_by(LiveUpdateEvent.id.desc())
        .offset(MAX_LIVE_EVENTS_PER_FAMILY)
        .limit(LIVE_EVENT_TRIM_BATCH_SIZE + 1)
        .all()
    )
    # Erst blockweise räumen. Sonst würde nach Erreichen des Limits bei jedem
    # einzelnen neuen Event genau eine Zeile gelöscht.
    if len(stale_rows) <= LIVE_EVENT_TRIM_BATCH_SIZE:
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
