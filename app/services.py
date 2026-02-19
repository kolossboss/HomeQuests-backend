import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import LiveUpdateEvent, PointsLedger


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
) -> None:
    db.add(
        LiveUpdateEvent(
            family_id=family_id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        )
    )


def parse_live_payload(payload_json: str | None) -> dict:
    if not payload_json:
        return {}
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
