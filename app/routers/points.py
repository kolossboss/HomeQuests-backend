from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import FamilyMembership, PointsLedger, PointsSourceEnum, RoleEnum, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import BalanceItemOut, BalanceOut, LedgerEntryOut, PointsAdjustRequest
from ..services import emit_live_event, get_points_balance

router = APIRouter(tags=["points"])


@router.get("/families/{family_id}/points/balance/{user_id}", response_model=BalanceOut)
def get_balance(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    if current_user.id != user_id:
        require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    return BalanceOut(family_id=family_id, user_id=user_id, balance=get_points_balance(db, family_id, user_id))


@router.get("/families/{family_id}/points/ledger", response_model=list[LedgerEntryOut])
def list_ledger(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    return (
        db.query(PointsLedger)
        .filter(PointsLedger.family_id == family_id)
        .order_by(PointsLedger.created_at.desc())
        .limit(200)
        .all()
    )


@router.get("/families/{family_id}/points/ledger/{user_id}", response_model=list[LedgerEntryOut])
def list_user_ledger(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    if context.role == RoleEnum.child and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Keine Berechtigung")

    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht in der Familie")

    return (
        db.query(PointsLedger)
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .order_by(PointsLedger.created_at.desc())
        .limit(200)
        .all()
    )


@router.get("/families/{family_id}/points/balances", response_model=list[BalanceItemOut])
def list_balances(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    memberships = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id)
        .order_by(User.display_name.asc())
        .all()
    )

    if context.role == RoleEnum.child:
        memberships = [item for item in memberships if item[1].id == current_user.id]

    return [
        BalanceItemOut(
            family_id=family_id,
            user_id=user.id,
            display_name=user.display_name,
            role=membership.role,
            balance=get_points_balance(db, family_id, user.id),
        )
        for membership, user in memberships
    ]


@router.post("/families/{family_id}/points/adjust", response_model=LedgerEntryOut)
def adjust_points(
    family_id: int,
    payload: PointsAdjustRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    if payload.points_delta == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Punkte-Differenz darf nicht 0 sein")

    membership = (
        db.query(FamilyMembership)
        .filter(FamilyMembership.family_id == family_id, FamilyMembership.user_id == payload.user_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht in der Familie")

    entry = PointsLedger(
        family_id=family_id,
        user_id=payload.user_id,
        source_type=PointsSourceEnum.manual_adjustment,
        source_id=payload.user_id,
        points_delta=payload.points_delta,
        description=payload.description,
        created_by_id=current_user.id,
    )
    db.add(entry)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="points.adjusted",
        payload={"user_id": payload.user_id, "points_delta": payload.points_delta, "entry_id": entry.id},
    )
    db.commit()
    db.refresh(entry)
    return entry
