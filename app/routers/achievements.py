from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..achievement_engine import (
    build_achievement_overview,
    claim_achievement_profile,
    claim_achievement_reward,
    evaluate_achievements_for_user,
    list_freeze_windows,
)
from ..database import get_db
from ..deps import get_current_user
from ..models import AchievementFreezeWindow, FamilyMembership, RoleEnum, User
from ..rbac import get_membership_or_403, require_roles
from ..schemas import AchievementClaimOut, AchievementFreezeWindowCreate, AchievementFreezeWindowOut, AchievementOverviewOut

router = APIRouter(tags=["achievements"])


def _ensure_target_user_in_family(db: Session, family_id: int, user_id: int) -> FamilyMembership:
    membership = (
        db.query(FamilyMembership)
        .filter(
            FamilyMembership.family_id == family_id,
            FamilyMembership.user_id == user_id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nutzer nicht in der Familie")
    return membership


def _assert_can_view_target(context, current_user: User, target_user_id: int) -> None:
    if context.role == RoleEnum.child and current_user.id != target_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Keine Berechtigung")


@router.get("/families/{family_id}/achievements/me", response_model=AchievementOverviewOut)
def get_my_achievements(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    payload = build_achievement_overview(db, family_id, current_user.id)
    db.commit()
    return payload


@router.get("/families/{family_id}/achievements/users/{user_id}", response_model=AchievementOverviewOut)
def get_user_achievements(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    _assert_can_view_target(context, current_user, user_id)
    _ensure_target_user_in_family(db, family_id, user_id)
    payload = build_achievement_overview(db, family_id, user_id)
    db.commit()
    return payload


@router.post("/families/{family_id}/achievements/users/{user_id}/evaluate", response_model=AchievementOverviewOut)
def evaluate_user_achievements(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})
    _ensure_target_user_in_family(db, family_id, user_id)
    evaluate_achievements_for_user(
        db,
        family_id=family_id,
        user_id=user_id,
        triggered_by_id=current_user.id,
        reason="manual_rebuild",
        emit_events=True,
    )
    payload = build_achievement_overview(db, family_id, user_id)
    db.commit()
    return payload


@router.post("/families/{family_id}/achievements/{achievement_id}/claim-profile", response_model=AchievementClaimOut)
def claim_my_achievement_profile(
    family_id: int,
    achievement_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    try:
        claim_achievement_profile(
            db,
            family_id=family_id,
            user_id=current_user.id,
            achievement_id=achievement_id,
            triggered_by_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    overview = build_achievement_overview(db, family_id, current_user.id)
    db.commit()
    return AchievementClaimOut(
        overview=AchievementOverviewOut(**overview),
        achievement_id=achievement_id,
        profile_claimed=True,
        reward_claimed=False,
        points_delta=0,
    )


@router.post("/families/{family_id}/achievements/{achievement_id}/claim-reward", response_model=AchievementClaimOut)
def claim_my_achievement_reward(
    family_id: int,
    achievement_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    get_membership_or_403(db, family_id, current_user.id)
    try:
        _, points_delta = claim_achievement_reward(
            db,
            family_id=family_id,
            user_id=current_user.id,
            achievement_id=achievement_id,
            triggered_by_id=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    overview = build_achievement_overview(db, family_id, current_user.id)
    db.commit()
    return AchievementClaimOut(
        overview=AchievementOverviewOut(**overview),
        achievement_id=achievement_id,
        profile_claimed=True,
        reward_claimed=True,
        points_delta=points_delta,
    )


@router.get("/families/{family_id}/achievements/users/{user_id}/freeze-windows", response_model=list[AchievementFreezeWindowOut])
def get_achievement_freezes(
    family_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    _assert_can_view_target(context, current_user, user_id)
    _ensure_target_user_in_family(db, family_id, user_id)
    return list_freeze_windows(db, family_id, user_id)


@router.post("/families/{family_id}/achievements/users/{user_id}/freeze-windows", response_model=AchievementFreezeWindowOut)
def create_achievement_freeze(
    family_id: int,
    user_id: int,
    payload: AchievementFreezeWindowCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    require_roles(context, {RoleEnum.admin, RoleEnum.parent})
    _ensure_target_user_in_family(db, family_id, user_id)

    freeze = AchievementFreezeWindow(
        family_id=family_id,
        user_id=user_id,
        scope=payload.scope,
        reason=payload.reason,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        created_by_id=current_user.id,
    )
    db.add(freeze)
    db.flush()
    evaluate_achievements_for_user(
        db,
        family_id=family_id,
        user_id=user_id,
        triggered_by_id=current_user.id,
        reason="freeze_updated",
        emit_events=False,
    )
    db.commit()
    db.refresh(freeze)
    return freeze
