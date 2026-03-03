from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import FamilyMembership, RoleEnum, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import SystemTestNotificationRequest, SystemTestNotificationOut
from ..services import emit_live_event

router = APIRouter(tags=["system"])


@router.post("/families/{family_id}/system/test-notification", response_model=SystemTestNotificationOut)
def send_system_test_notification(
    family_id: int,
    payload: SystemTestNotificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    recipients = (
        db.query(User)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(
            FamilyMembership.family_id == family_id,
            User.is_active == True,  # noqa: E712
        )
        .order_by(User.display_name.asc())
        .all()
    )
    if not recipients:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Keine aktiven Nutzer gefunden")

    recipients_by_id = {entry.id: entry for entry in recipients}
    if payload.recipient_user_ids is None:
        selected_recipients = recipients
    else:
        missing_user_ids = [entry for entry in payload.recipient_user_ids if entry not in recipients_by_id]
        if missing_user_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Ungültige Empfänger-ID(s): {missing_user_ids}",
            )
        selected_recipients = [recipients_by_id[entry] for entry in payload.recipient_user_ids]

    if not selected_recipients:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mindestens ein Empfänger muss ausgewählt sein")

    recipient_user_ids = [entry.id for entry in selected_recipients]
    recipient_display_names = [entry.display_name for entry in selected_recipients]
    sent_at = datetime.utcnow().isoformat()

    emit_live_event(
        db,
        family_id=family_id,
        event_type="notification.test",
        payload={
            "title": payload.title,
            "message": payload.message,
            "requested_by_id": current_user.id,
            "recipient_user_ids": recipient_user_ids,
            "sent_at": sent_at,
        },
    )
    db.commit()

    return SystemTestNotificationOut(
        sent=True,
        family_id=family_id,
        title=payload.title,
        message=payload.message,
        recipient_count=len(recipient_user_ids),
        recipient_user_ids=recipient_user_ids,
        recipient_display_names=recipient_display_names,
        delivery_mode="live_event",
        event_type="notification.test",
        sent_at=sent_at,
    )
