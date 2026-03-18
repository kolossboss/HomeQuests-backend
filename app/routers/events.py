from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import CalendarEvent, FamilyMembership, RoleEnum, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import CalendarEventCreate, CalendarEventOut
from ..services import emit_live_event

router = APIRouter(tags=["events"])


@router.get("/families/{family_id}/events", response_model=list[CalendarEventOut])
def list_events(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    return (
        db.query(CalendarEvent)
        .filter(CalendarEvent.family_id == family_id)
        .order_by(CalendarEvent.start_at.asc())
        .all()
    )


@router.post("/families/{family_id}/events", response_model=CalendarEventOut)
def create_event(
    family_id: int,
    payload: CalendarEventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    if payload.end_at <= payload.start_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ende muss nach Start liegen")

    if payload.responsible_user_id is not None:
        responsible = (
            db.query(FamilyMembership)
            .filter(
                FamilyMembership.family_id == family_id,
                FamilyMembership.user_id == payload.responsible_user_id,
            )
            .first()
        )
        if not responsible:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verantwortlicher Benutzer ist nicht in der Familie")

    event = CalendarEvent(
        family_id=family_id,
        title=payload.title,
        description=payload.description,
        responsible_user_id=payload.responsible_user_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        created_by_id=current_user.id,
    )
    db.add(event)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="event.created",
        payload={"event_id": event.id, "responsible_user_id": event.responsible_user_id},
    )
    db.commit()
    db.refresh(event)
    return event
