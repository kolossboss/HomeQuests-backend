from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import (
    PointsLedger,
    PointsSourceEnum,
    RedemptionStatusEnum,
    Reward,
    RewardContribution,
    RewardContributionStatusEnum,
    RewardRedemption,
    RoleEnum,
    User,
)
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    RedemptionOut,
    RedemptionRequest,
    RedemptionReviewRequest,
    RewardContributionProgressItemOut,
    RewardContributionProgressOut,
    RewardContributionRequest,
    RewardCreate,
    RewardOut,
    RewardUpdate,
)
from ..services import emit_live_event, get_points_balance

router = APIRouter(tags=["rewards"])

ACTIVE_CONTRIBUTION_STATUSES = {
    RewardContributionStatusEnum.reserved,
    RewardContributionStatusEnum.submitted,
}


def _reserved_points_for_redemption(db: Session, family_id: int, user_id: int, redemption_id: int) -> int:
    result = (
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(
            PointsLedger.family_id == family_id,
            PointsLedger.user_id == user_id,
            PointsLedger.source_type == PointsSourceEnum.reward_redemption,
            PointsLedger.source_id == redemption_id,
        )
        .scalar()
    )
    # Reservierungen sind negative Deltas.
    return abs(min(int(result or 0), 0))


def _pending_redemption_for_reward(db: Session, family_id: int, reward_id: int) -> RewardRedemption | None:
    return (
        db.query(RewardRedemption)
        .join(Reward, Reward.id == RewardRedemption.reward_id)
        .filter(
            Reward.family_id == family_id,
            RewardRedemption.reward_id == reward_id,
            RewardRedemption.status == RedemptionStatusEnum.pending,
        )
        .order_by(RewardRedemption.requested_at.desc())
        .first()
    )


def _build_contribution_progress(db: Session, reward: Reward) -> RewardContributionProgressOut:
    pending_redemption = _pending_redemption_for_reward(db, reward.family_id, reward.id)
    rows = (
        db.query(RewardContribution, User)
        .join(User, User.id == RewardContribution.user_id)
        .filter(
            RewardContribution.family_id == reward.family_id,
            RewardContribution.reward_id == reward.id,
            RewardContribution.status.in_(list(ACTIVE_CONTRIBUTION_STATUSES)),
        )
        .order_by(RewardContribution.created_at.asc())
        .all()
    )
    total_reserved = sum(int(contribution.points_reserved) for contribution, _ in rows)
    remaining_points = max(reward.cost_points - total_reserved, 0)
    return RewardContributionProgressOut(
        reward_id=reward.id,
        reward_title=reward.title,
        cost_points=reward.cost_points,
        total_reserved=total_reserved,
        remaining_points=remaining_points,
        pending_redemption_id=pending_redemption.id if pending_redemption else None,
        contributions=[
            RewardContributionProgressItemOut(
                id=contribution.id,
                user_id=contribution.user_id,
                user_name=user.display_name,
                points_reserved=contribution.points_reserved,
                status=contribution.status,
                created_at=contribution.created_at,
            )
            for contribution, user in rows
        ],
    )


@router.get("/families/{family_id}/rewards", response_model=list[RewardOut])
def list_rewards(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    return db.query(Reward).filter(Reward.family_id == family_id).order_by(Reward.created_at.desc()).all()


@router.get("/families/{family_id}/rewards/{reward_id}/contributions", response_model=RewardContributionProgressOut)
def get_reward_contribution_progress(
    family_id: int,
    reward_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    reward = db.query(Reward).filter(Reward.id == reward_id, Reward.family_id == family_id).first()
    if not reward:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belohnung nicht gefunden")
    if not reward.is_shareable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diese Belohnung ist nicht aufteilbar")
    return _build_contribution_progress(db, reward)


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
        is_shareable=payload.is_shareable,
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
    reward.is_shareable = payload.is_shareable
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


@router.post("/rewards/{reward_id}/contribute", response_model=RewardContributionProgressOut)
def contribute_reward(
    reward_id: int,
    payload: RewardContributionRequest,
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
    if not reward.is_shareable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diese Belohnung ist nicht aufteilbar")

    pending_redemption = _pending_redemption_for_reward(db, reward.family_id, reward.id)
    if pending_redemption:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für diese Belohnung läuft bereits eine Anfrage")

    progress_before = _build_contribution_progress(db, reward)
    if progress_before.remaining_points <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für diese Belohnung sind bereits genug Punkte reserviert")
    if payload.points > progress_before.remaining_points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Zu viele Punkte für diesen Beitrag. Maximal möglich: {progress_before.remaining_points}",
        )

    balance = get_points_balance(db, reward.family_id, current_user.id)
    if balance < payload.points:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Nicht genug Punkte. Verfügbar: {balance}, angefragt: {payload.points}",
        )

    contribution = RewardContribution(
        family_id=reward.family_id,
        reward_id=reward.id,
        user_id=current_user.id,
        points_reserved=payload.points,
        status=RewardContributionStatusEnum.reserved,
        redemption_id=None,
    )
    db.add(contribution)
    db.flush()

    db.add(
        PointsLedger(
            family_id=reward.family_id,
            user_id=current_user.id,
            source_type=PointsSourceEnum.reward_contribution,
            source_id=contribution.id,
            points_delta=-payload.points,
            description=f"Sammelbeitrag für Belohnung: {reward.title}",
            created_by_id=current_user.id,
        )
    )

    total_reserved = progress_before.total_reserved + payload.points
    if total_reserved >= reward.cost_points:
        redemption = RewardRedemption(
            reward_id=reward.id,
            requested_by_id=current_user.id,
            comment=payload.comment,
            status=RedemptionStatusEnum.pending,
        )
        db.add(redemption)
        db.flush()

        open_contributions = (
            db.query(RewardContribution)
            .filter(
                RewardContribution.family_id == reward.family_id,
                RewardContribution.reward_id == reward.id,
                RewardContribution.status == RewardContributionStatusEnum.reserved,
                RewardContribution.redemption_id.is_(None),
            )
            .order_by(RewardContribution.created_at.asc())
            .all()
        )
        for entry in open_contributions:
            entry.status = RewardContributionStatusEnum.submitted
            entry.redemption_id = redemption.id

        emit_live_event(
            db,
            family_id=reward.family_id,
            event_type="reward.redeem_requested",
            payload={"redemption_id": redemption.id, "reward_id": reward.id, "requested_by_id": current_user.id},
        )

    emit_live_event(
        db,
        family_id=reward.family_id,
        event_type="reward.contribution.updated",
        payload={"reward_id": reward.id, "user_id": current_user.id, "points": payload.points},
    )
    db.commit()
    return _build_contribution_progress(db, reward)


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
    if reward.is_shareable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Diese Belohnung ist aufteilbar. Bitte Beiträge nutzen")

    if _pending_redemption_for_reward(db, reward.family_id, reward.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Für diese Belohnung läuft bereits eine Anfrage")

    active_contribution_count = (
        db.query(func.count(RewardContribution.id))
        .filter(
            RewardContribution.family_id == reward.family_id,
            RewardContribution.reward_id == reward.id,
            RewardContribution.status.in_(list(ACTIVE_CONTRIBUTION_STATUSES)),
        )
        .scalar()
    )
    if int(active_contribution_count or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Für diese Belohnung laufen bereits Sammelbeiträge",
        )

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

    # Punkte sofort reservieren, damit keine Mehrfachanfragen über das verfügbare Guthaben hinaus möglich sind.
    db.add(
        PointsLedger(
            family_id=reward.family_id,
            user_id=current_user.id,
            source_type=PointsSourceEnum.reward_redemption,
            source_id=redemption.id,
            points_delta=-reward.cost_points,
            description=f"Reserviert für Belohnung: {reward.title}",
            created_by_id=current_user.id,
        )
    )

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

    contributed_redemption_ids_subquery = (
        db.query(RewardContribution.redemption_id)
        .filter(
            RewardContribution.family_id == family_id,
            RewardContribution.user_id == current_user.id,
            RewardContribution.redemption_id.is_not(None),
        )
        .subquery()
    )
    return (
        db.query(RewardRedemption)
        .join(Reward, Reward.id == RewardRedemption.reward_id)
        .filter(Reward.family_id == family_id)
        .filter(
            or_(
                RewardRedemption.requested_by_id == current_user.id,
                RewardRedemption.id.in_(contributed_redemption_ids_subquery),
            )
        )
        .distinct()
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

    linked_contributions = (
        db.query(RewardContribution)
        .filter(
            RewardContribution.redemption_id == redemption.id,
            RewardContribution.status.in_([RewardContributionStatusEnum.submitted, RewardContributionStatusEnum.reserved]),
        )
        .order_by(RewardContribution.created_at.asc())
        .all()
    )
    reserved_points = _reserved_points_for_redemption(
        db,
        family_id=reward.family_id,
        user_id=redemption.requested_by_id,
        redemption_id=redemption.id,
    )

    if payload.decision == RedemptionStatusEnum.approved:
        if linked_contributions:
            contributed_total = sum(int(entry.points_reserved) for entry in linked_contributions)
            if contributed_total < reward.cost_points:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Sammelbeiträge sind unvollständig ({contributed_total}/{reward.cost_points})",
                )
            for entry in linked_contributions:
                entry.status = RewardContributionStatusEnum.consumed
        else:
            # Rückwärtskompatibel: alte Anfragen hatten ggf. noch keine Reservierung.
            missing_points = max(reward.cost_points - reserved_points, 0)
            if missing_points > 0:
                balance = get_points_balance(db, reward.family_id, redemption.requested_by_id)
                if balance < missing_points:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nicht genug Punkte")

                db.add(
                    PointsLedger(
                        family_id=reward.family_id,
                        user_id=redemption.requested_by_id,
                        source_type=PointsSourceEnum.reward_redemption,
                        source_id=redemption.id,
                        points_delta=-missing_points,
                        description=f"Einlösung: {reward.title}",
                        created_by_id=current_user.id,
                    )
                )
    else:
        if linked_contributions:
            for entry in linked_contributions:
                if entry.points_reserved > 0:
                    db.add(
                        PointsLedger(
                            family_id=reward.family_id,
                            user_id=entry.user_id,
                            source_type=PointsSourceEnum.reward_contribution,
                            source_id=entry.id,
                            points_delta=entry.points_reserved,
                            description=f"Sammelbeitrag freigegeben: {reward.title}",
                            created_by_id=current_user.id,
                        )
                    )
                entry.status = RewardContributionStatusEnum.released

        if reserved_points > 0:
            db.add(
                PointsLedger(
                    family_id=reward.family_id,
                    user_id=redemption.requested_by_id,
                    source_type=PointsSourceEnum.reward_redemption,
                    source_id=redemption.id,
                    points_delta=reserved_points,
                    description=f"Reservierung freigegeben: {reward.title}",
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
    if linked_contributions:
        emit_live_event(
            db,
            family_id=reward.family_id,
            event_type="reward.contribution.updated",
            payload={"reward_id": reward.id, "redemption_id": redemption.id, "status": redemption.status.value},
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
