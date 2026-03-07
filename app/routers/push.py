from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import FamilyMembership, PushDevice, User
from ..schemas import PushDeviceOut, PushDeviceRegisterRequest, PushDeviceUnregisterRequest

router = APIRouter(tags=["push"])


def _family_id_for_user(db: Session, user_id: int) -> int:
    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.user_id == user_id)
        .order_by(FamilyMembership.family_id.asc())
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Keine Familie für Benutzer gefunden")
    return int(membership.family_id)


def _mask_device_token(token: str) -> str:
    normalized = (token or "").strip()
    if not normalized:
        return ""
    if len(normalized) <= 10:
        return "*" * len(normalized)
    return f"{normalized[:6]}...{normalized[-4:]}"


@router.post("/push/devices/register", response_model=PushDeviceOut)
def register_push_device(
    payload: PushDeviceRegisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    family_id = _family_id_for_user(db, current_user.id)

    device = db.query(PushDevice).filter(PushDevice.device_token == payload.device_token).first()
    if device is None:
        device = PushDevice(
            family_id=family_id,
            user_id=current_user.id,
            device_token=payload.device_token,
            platform="ios",
            bundle_id=payload.bundle_id,
            push_environment=payload.push_environment,
            notifications_enabled=payload.notifications_enabled,
            child_new_task=payload.child_new_task,
            manager_task_submitted=payload.manager_task_submitted,
            manager_reward_requested=payload.manager_reward_requested,
            task_due_reminder=payload.task_due_reminder,
            last_seen_at=datetime.utcnow(),
        )
        db.add(device)
    else:
        device.family_id = family_id
        device.user_id = current_user.id
        device.platform = "ios"
        device.bundle_id = payload.bundle_id
        device.push_environment = payload.push_environment
        device.notifications_enabled = payload.notifications_enabled
        device.child_new_task = payload.child_new_task
        device.manager_task_submitted = payload.manager_task_submitted
        device.manager_reward_requested = payload.manager_reward_requested
        device.task_due_reminder = payload.task_due_reminder
        device.last_seen_at = datetime.utcnow()

    db.commit()
    db.refresh(device)
    return PushDeviceOut(
        id=device.id,
        family_id=device.family_id,
        user_id=device.user_id,
        device_token=_mask_device_token(device.device_token),
        platform=device.platform,
        bundle_id=device.bundle_id,
        push_environment=device.push_environment,
        notifications_enabled=device.notifications_enabled,
        child_new_task=device.child_new_task,
        manager_task_submitted=device.manager_task_submitted,
        manager_reward_requested=device.manager_reward_requested,
        task_due_reminder=device.task_due_reminder,
        last_seen_at=device.last_seen_at,
    )


@router.post("/push/devices/unregister")
def unregister_push_device(
    payload: PushDeviceUnregisterRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    device = (
        db.query(PushDevice)
        .filter(
            PushDevice.device_token == payload.device_token,
            PushDevice.user_id == current_user.id,
        )
        .first()
    )
    if device is None:
        return {"deleted": False}

    db.delete(device)
    db.commit()
    return {"deleted": True}
