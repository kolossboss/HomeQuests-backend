from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Family, FamilyMembership, RoleEnum, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import FamilyMemberOut, FamilyOut, MemberCreate, MemberUpdate
from ..security import hash_password
from ..services import emit_live_event

router = APIRouter(prefix="/families", tags=["families"])


def _serialize_family_member(
    membership: FamilyMembership,
    user: User,
    viewer_role: RoleEnum,
    viewer_user_id: int,
) -> FamilyMemberOut:
    email = user.email
    ha_notify_service = user.ha_notify_service
    ha_notifications_enabled = bool(user.ha_notifications_enabled)
    ha_child_new_task = bool(user.ha_child_new_task)
    ha_manager_task_submitted = bool(user.ha_manager_task_submitted)
    ha_manager_reward_requested = bool(user.ha_manager_reward_requested)
    ha_task_due_reminder = bool(user.ha_task_due_reminder)
    role = membership.role
    if viewer_role == RoleEnum.child and user.id != viewer_user_id:
        email = None
        ha_notify_service = None
        ha_notifications_enabled = False
        ha_child_new_task = True
        ha_manager_task_submitted = True
        ha_manager_reward_requested = True
        ha_task_due_reminder = True
        role = RoleEnum.child

    return FamilyMemberOut(
        membership_id=membership.id,
        family_id=membership.family_id,
        user_id=user.id,
        display_name=user.display_name,
        email=email,
        ha_notify_service=ha_notify_service,
        ha_notifications_enabled=ha_notifications_enabled,
        ha_child_new_task=ha_child_new_task,
        ha_manager_task_submitted=ha_manager_task_submitted,
        ha_manager_reward_requested=ha_manager_reward_requested,
        ha_task_due_reminder=ha_task_due_reminder,
        is_active=user.is_active,
        role=role,
        created_at=membership.created_at,
    )


@router.get("/my", response_model=list[FamilyOut])
def my_families(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    family_ids = (
        db.query(FamilyMembership.family_id)
        .filter(FamilyMembership.user_id == current_user.id)
        .all()
    )
    ids = [item[0] for item in family_ids]
    if not ids:
        return []
    return db.query(Family).filter(Family.id.in_(ids)).all()


@router.get("/{family_id}/members", response_model=list[FamilyMemberOut])
def list_members(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    rows = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id)
        .order_by(User.display_name.asc())
        .all()
    )
    return [
        _serialize_family_member(membership, user, membership_context.role, current_user.id)
        for membership, user in rows
    ]


@router.post("/{family_id}/members", response_model=FamilyMemberOut)
def create_member(
    family_id: int,
    payload: MemberCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin})

    family = db.query(Family).filter(Family.id == family_id).first()
    if not family:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Familie nicht gefunden")

    normalized_email = payload.email.lower() if payload.email else None
    existing_user = db.query(User).filter(User.email == normalized_email).first() if normalized_email else None
    if existing_user:
        other_household = (
            db.query(FamilyMembership)
            .filter(FamilyMembership.user_id == existing_user.id, FamilyMembership.family_id != family_id)
            .first()
        )
        if other_household:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Benutzer gehört bereits zu einem anderen Haushalt",
            )
        already_member = (
            db.query(FamilyMembership)
            .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == existing_user.id)
            .first()
        )
        if already_member:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Benutzer ist bereits Mitglied")
        user = existing_user
        user.ha_notify_service = payload.ha_notify_service
        user.ha_notifications_enabled = payload.ha_notifications_enabled
        user.ha_child_new_task = payload.ha_child_new_task
        user.ha_manager_task_submitted = payload.ha_manager_task_submitted
        user.ha_manager_reward_requested = payload.ha_manager_reward_requested
        user.ha_task_due_reminder = payload.ha_task_due_reminder
    else:
        display_name_taken = (
            db.query(User)
            .filter(func.lower(User.display_name) == payload.display_name.lower())
            .first()
        )
        if display_name_taken:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Name ist bereits vergeben. Bitte einen anderen Namen wählen",
            )
        if not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passwort erforderlich für neue Benutzer",
            )
        user = User(
            email=normalized_email,
            display_name=payload.display_name,
            ha_notify_service=payload.ha_notify_service,
            ha_notifications_enabled=payload.ha_notifications_enabled,
            ha_child_new_task=payload.ha_child_new_task,
            ha_manager_task_submitted=payload.ha_manager_task_submitted,
            ha_manager_reward_requested=payload.ha_manager_reward_requested,
            ha_task_due_reminder=payload.ha_task_due_reminder,
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()

    new_membership = FamilyMembership(family_id=family_id, user_id=user.id, role=payload.role)
    db.add(new_membership)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="member.created",
        payload={"user_id": user.id, "role": payload.role.value},
    )
    db.commit()
    db.refresh(new_membership)
    db.refresh(user)

    return _serialize_family_member(new_membership, user, membership_context.role, current_user.id)


@router.put("/{family_id}/members/{user_id}", response_model=FamilyMemberOut)
def update_member(
    family_id: int,
    user_id: int,
    payload: MemberUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin})

    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mitglied nicht gefunden")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden")

    admins_count = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.role == RoleEnum.admin)
        .count()
    )
    if membership.role == RoleEnum.admin and payload.role != RoleEnum.admin and admins_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mindestens ein Admin muss in der Familie verbleiben",
        )

    duplicate_name = (
        db.query(User)
        .filter(func.lower(User.display_name) == payload.display_name.lower(), User.id != user.id)
        .first()
    )
    if duplicate_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name ist bereits vergeben. Bitte einen anderen Namen wählen",
        )

    user.display_name = payload.display_name
    user.ha_notify_service = payload.ha_notify_service
    if payload.ha_notifications_enabled is not None:
        user.ha_notifications_enabled = payload.ha_notifications_enabled
    if payload.ha_child_new_task is not None:
        user.ha_child_new_task = payload.ha_child_new_task
    if payload.ha_manager_task_submitted is not None:
        user.ha_manager_task_submitted = payload.ha_manager_task_submitted
    if payload.ha_manager_reward_requested is not None:
        user.ha_manager_reward_requested = payload.ha_manager_reward_requested
    if payload.ha_task_due_reminder is not None:
        user.ha_task_due_reminder = payload.ha_task_due_reminder
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    membership.role = payload.role

    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="member.updated",
        payload={"user_id": user.id, "role": payload.role.value, "is_active": user.is_active},
    )
    db.commit()
    db.refresh(membership)
    db.refresh(user)

    return _serialize_family_member(membership, user, membership_context.role, current_user.id)


@router.delete("/{family_id}/members/{user_id}")
def delete_member(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin})

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Du kannst dein eigenes Admin-Mitglied hier nicht löschen",
        )

    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mitglied nicht gefunden")

    if membership.role == RoleEnum.admin:
        admins_count = (
            db.query(FamilyMembership)
            .filter(FamilyMembership.family_id == family_id, FamilyMembership.role == RoleEnum.admin)
            .count()
        )
        if admins_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mindestens ein Admin muss in der Familie verbleiben",
            )

    user_id_value = membership.user_id
    db.delete(membership)
    emit_live_event(
        db,
        family_id=family_id,
        event_type="member.deleted",
        payload={"user_id": user_id_value},
    )
    db.commit()
    return {"deleted": True}
