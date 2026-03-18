from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import PointsLedger


def get_points_balance(db: Session, family_id: int, user_id: int) -> int:
    result = (
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .scalar()
    )
    return int(result or 0)
