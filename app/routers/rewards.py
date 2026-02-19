from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import (
    PointsLedger,
    PointsSourceEnum,
    RedemptionStatusEnum,
    Reward,
    RewardRedemption,
    RoleEnum,
    User,
)
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    RedemptionOut,
    RedemptionRequest,
    RedemptionReviewRequest,
    RewardCreate,
    RewardOut,
    RewardUpdate,
)
from ..services import emit_live_event, get_points_balance

router = APIRouter(tags=["rewards"])


@router.get("/families/{family_id}/rewards", response_model=list[RewardOut])
def list_rewards(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    return db.query(Reward).filter(Reward.family_id == family_id).order_by(Reward.created_at.desc()).all()


@router.post("/families/{family_id}/rewards", response_model=RewardOut)
def create_reward(
    family_id: int,
    payload: RewardCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership_context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(membership_context, {RoleEnum.admin, RoleEnum.parent})

    reward = Reward(
        family_id=family_id,
        title=payload.title,
        description=payload.description,
        cost_points=payload.cost_points,
        is_active=payload.is_active,
        created_by_id=current_user.id,
    )
    db.add(reward)
    db.flush()
    emit_live_event(
        db,
        family_id=family_id,
        event_type="reward.created",
        payload={"reward_id": reward.id},
    )
    db.commit()
    db.refresh(reward)
    return reward


@router.put("/rewards/{reward_id}", response_model=RewardOut)
def update_reward(
    reward_id: int,
    payload: RewardUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belohnung nicht gefunden")

    context = get_membership_or_403(db, reward.family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    reward.title = payload.title
    reward.description = payload.description
    reward.cost_points = payload.cost_points
    reward.is_active = payload.is_active

    db.flush()
    emit_live_event(
        db,
        family_id=reward.family_id,
        event_type="reward.updated",
        payload={"reward_id": reward.id},
    )
    db.commit()
    db.refresh(reward)
    return reward


@router.delete("/rewards/{reward_id}")
def delete_reward(
    reward_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belohnung nicht gefunden")

    context = get_membership_or_403(db, reward.family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    reward_id_value = reward.id
    family_id_value = reward.family_id
    db.delete(reward)
    emit_live_event(
        db,
        family_id=family_id_value,
        event_type="reward.deleted",
        payload={"reward_id": reward_id_value},
    )
    db.commit()
    return {"deleted": True}


@router.post("/rewards/{reward_id}/redeem")
def redeem_reward(
    reward_id: int,
    payload: RedemptionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reward = db.query(Reward).filter(Reward.id == reward_id).first()
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belohnung nicht gefunden")

    context = get_membership_or_403(db, reward.family_id, current_user.id)
    require_roles(context, {RoleEnum.child})

    if not reward.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Belohnung ist deaktiviert")

    balance = get_points_balance(db, reward.family_id, current_user.id)
    if balance < reward.cost_points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nicht genug Punkte. Benötigt: {reward.cost_points}, verfügbar: {balance}",
        )

    redemption = RewardRedemption(
        reward_id=reward.id,
        requested_by_id=current_user.id,
        comment=payload.comment,
    )
    db.add(redemption)
    db.flush()
    emit_live_event(
        db,
        family_id=reward.family_id,
        event_type="reward.redeem_requested",
        payload={"redemption_id": redemption.id, "reward_id": reward.id, "requested_by_id": current_user.id},
    )
    db.commit()
    db.refresh(redemption)
    return {
        "id": redemption.id,
        "reward_id": redemption.reward_id,
        "requested_by_id": redemption.requested_by_id,
        "status": redemption.status,
        "requested_at": redemption.requested_at,
    }


@router.get("/families/{family_id}/redemptions", response_model=list[RedemptionOut])
def list_redemptions(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    if context.role in {RoleEnum.admin, RoleEnum.parent}:
        return (
            db.query(RewardRedemption)
            .join(Reward, Reward.id == RewardRedemption.reward_id)
            .filter(Reward.family_id == family_id)
            .order_by(RewardRedemption.requested_at.desc())
            .all()
        )

    return (
        db.query(RewardRedemption)
        .join(Reward, Reward.id == RewardRedemption.reward_id)
        .filter(Reward.family_id == family_id, RewardRedemption.requested_by_id == current_user.id)
        .order_by(RewardRedemption.requested_at.desc())
        .all()
    )


@router.post("/redemptions/{redemption_id}/review")
def review_redemption(
    redemption_id: int,
    payload: RedemptionReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    redemption = db.query(RewardRedemption).filter(RewardRedemption.id == redemption_id).first()
    if not redemption:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Einlösung nicht gefunden")

    reward = db.query(Reward).filter(Reward.id == redemption.reward_id).first()
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belohnung nicht gefunden")

    context = get_membership_or_403(db, reward.family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})

    if redemption.status != RedemptionStatusEnum.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Einlösung wurde bereits bearbeitet")

    if payload.decision not in {RedemptionStatusEnum.approved, RedemptionStatusEnum.rejected}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ungültige Entscheidung")

    if payload.decision == RedemptionStatusEnum.approved:
        balance = get_points_balance(db, reward.family_id, redemption.requested_by_id)
        if balance < reward.cost_points:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nicht genug Punkte")

        db.add(
            PointsLedger(
                family_id=reward.family_id,
                user_id=redemption.requested_by_id,
                source_type=PointsSourceEnum.reward_redemption,
                source_id=redemption.id,
                points_delta=-reward.cost_points,
                description=f"Einlösung: {reward.title}",
                created_by_id=current_user.id,
            )
        )

    redemption.status = payload.decision
    redemption.comment = payload.comment
    redemption.reviewed_by_id = current_user.id
    redemption.reviewed_at = datetime.utcnow()

    db.flush()
    emit_live_event(
        db,
        family_id=reward.family_id,
        event_type="reward.redeem_reviewed",
        payload={"redemption_id": redemption.id, "status": redemption.status.value, "requested_by_id": redemption.requested_by_id},
    )
    db.commit()
    db.refresh(redemption)

    return {
        "id": redemption.id,
        "reward_id": redemption.reward_id,
        "requested_by_id": redemption.requested_by_id,
        "status": redemption.status,
        "reviewed_at": redemption.reviewed_at,
    }
