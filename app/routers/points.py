from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import (
    FamilyMembership,
    PointsLedger,
    PointsSourceEnum,
    RedemptionStatusEnum,
    Reward,
    RewardContribution,
    RewardRedemption,
    RoleEnum,
    User,
)
from ..rbac import get_membership_or_403, require_roles
from ..schemas import (
    BalanceItemOut,
    BalanceOut,
    ChildPointsStatsOut,
    LedgerEntryOut,
    PointsAdjustRequest,
    PointsRewardRequestStatOut,
    PointsRewardSpendStatOut,
    PointsTrendBucketOut,
)
from ..services import emit_live_event, get_points_balance

router = APIRouter(tags=["points"])

TREND_EARNED_SOURCES = {PointsSourceEnum.task_approval, PointsSourceEnum.manual_adjustment}
TREND_SPENT_SOURCES = {PointsSourceEnum.reward_redemption, PointsSourceEnum.reward_contribution}


def _family_user_name_map(db: Session, family_id: int) -> dict[int, str]:
    rows = (
        db.query(User.id, User.display_name)
        .join(FamilyMembership, FamilyMembership.user_id == User.id)
        .filter(FamilyMembership.family_id == family_id)
        .all()
    )
    return {int(user_id): str(display_name) for user_id, display_name in rows}


def _to_ledger_out(entry: PointsLedger, user_names: dict[int, str]) -> LedgerEntryOut:
    source_type_value = entry.source_type.value if hasattr(entry.source_type, "value") else str(entry.source_type)
    return LedgerEntryOut(
        id=entry.id,
        family_id=entry.family_id,
        user_id=entry.user_id,
        user_display_name=user_names.get(entry.user_id),
        source_type=source_type_value,
        source_id=entry.source_id,
        points_delta=entry.points_delta,
        description=entry.description,
        created_at=entry.created_at,
    )


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(value: date, months: int) -> date:
    year = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    return date(year, month, 1)


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _safe_average(value: int, divisor: float) -> float:
    if divisor <= 0:
        return 0.0
    return round(float(value) / float(divisor), 2)


def _is_earned_points_delta(delta: int, source_type: PointsSourceEnum) -> bool:
    return delta > 0 and source_type in TREND_EARNED_SOURCES


def _is_spent_points_delta(delta: int, source_type: PointsSourceEnum) -> bool:
    return delta < 0 and source_type in TREND_SPENT_SOURCES


def _build_day_trend(
    rows: list[tuple[date, int, PointsSourceEnum]],
    today: date,
    *,
    days: int = 14,
) -> list[PointsTrendBucketOut]:
    day_map: dict[date, dict[str, int]] = defaultdict(lambda: {"earned": 0, "spent": 0, "net": 0})
    for day_value, delta, source_type in rows:
        bucket = day_map[day_value]
        bucket["net"] += int(delta)
        if _is_earned_points_delta(delta, source_type):
            bucket["earned"] += int(delta)
        elif _is_spent_points_delta(delta, source_type):
            bucket["spent"] += int(abs(delta))

    start_day = today - timedelta(days=days - 1)
    result: list[PointsTrendBucketOut] = []
    for offset in range(days):
        bucket_day = start_day + timedelta(days=offset)
        values = day_map.get(bucket_day, {"earned": 0, "spent": 0, "net": 0})
        result.append(
            PointsTrendBucketOut(
                bucket_key=bucket_day.isoformat(),
                label=bucket_day.strftime("%d.%m"),
                earned_points=int(values["earned"]),
                spent_points=int(values["spent"]),
                net_points=int(values["net"]),
            )
        )
    return result


def _build_week_trend(
    rows: list[tuple[date, int, PointsSourceEnum]],
    today: date,
    *,
    weeks: int = 12,
) -> list[PointsTrendBucketOut]:
    week_map: dict[date, dict[str, int]] = defaultdict(lambda: {"earned": 0, "spent": 0, "net": 0})
    for day_value, delta, source_type in rows:
        start = _week_start(day_value)
        bucket = week_map[start]
        bucket["net"] += int(delta)
        if _is_earned_points_delta(delta, source_type):
            bucket["earned"] += int(delta)
        elif _is_spent_points_delta(delta, source_type):
            bucket["spent"] += int(abs(delta))

    current_week = _week_start(today)
    start_week = current_week - timedelta(weeks=weeks - 1)
    result: list[PointsTrendBucketOut] = []
    for offset in range(weeks):
        start = start_week + timedelta(weeks=offset)
        iso_week = start.isocalendar().week
        values = week_map.get(start, {"earned": 0, "spent": 0, "net": 0})
        result.append(
            PointsTrendBucketOut(
                bucket_key=start.isoformat(),
                label=f"KW {iso_week}",
                earned_points=int(values["earned"]),
                spent_points=int(values["spent"]),
                net_points=int(values["net"]),
            )
        )
    return result


def _build_month_trend(
    rows: list[tuple[date, int, PointsSourceEnum]],
    today: date,
    *,
    months: int = 12,
) -> list[PointsTrendBucketOut]:
    month_map: dict[date, dict[str, int]] = defaultdict(lambda: {"earned": 0, "spent": 0, "net": 0})
    for day_value, delta, source_type in rows:
        start = _month_start(day_value)
        bucket = month_map[start]
        bucket["net"] += int(delta)
        if _is_earned_points_delta(delta, source_type):
            bucket["earned"] += int(delta)
        elif _is_spent_points_delta(delta, source_type):
            bucket["spent"] += int(abs(delta))

    current_month = _month_start(today)
    start_month = _shift_month(current_month, -(months - 1))
    result: list[PointsTrendBucketOut] = []
    for offset in range(months):
        start = _shift_month(start_month, offset)
        values = month_map.get(start, {"earned": 0, "spent": 0, "net": 0})
        result.append(
            PointsTrendBucketOut(
                bucket_key=start.isoformat(),
                label=start.strftime("%m/%y"),
                earned_points=int(values["earned"]),
                spent_points=int(values["spent"]),
                net_points=int(values["net"]),
            )
        )
    return result


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

    entries = (
        db.query(PointsLedger)
        .filter(PointsLedger.family_id == family_id)
        .order_by(PointsLedger.created_at.desc())
        .limit(200)
        .all()
    )
    user_names = _family_user_name_map(db, family_id)
    return [_to_ledger_out(entry, user_names) for entry in entries]


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

    entries = (
        db.query(PointsLedger)
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .order_by(PointsLedger.created_at.desc())
        .limit(200)
        .all()
    )
    user_names = _family_user_name_map(db, family_id)
    return [_to_ledger_out(entry, user_names) for entry in entries]


@router.get("/families/{family_id}/points/balances", response_model=list[BalanceItemOut])
def list_balances(
    family_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    context = get_membership_or_403(db, family_id, current_user.id)
    memberships_and_users = (
        db.query(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .filter(FamilyMembership.family_id == family_id)
        .order_by(User.display_name.asc())
        .all()
    )

    if context.role == RoleEnum.child:
        memberships_and_users = [item for item in memberships_and_users if item[1].id == current_user.id]

    user_ids = [user.id for _, user in memberships_and_users]
    balances_by_user: dict[int, int] = {}
    if user_ids:
        rows = (
            db.query(
                PointsLedger.user_id,
                func.coalesce(func.sum(PointsLedger.points_delta), 0),
            )
            .filter(
                PointsLedger.family_id == family_id,
                PointsLedger.user_id.in_(user_ids),
            )
            .group_by(PointsLedger.user_id)
            .all()
        )
        balances_by_user = {int(user_id): int(balance or 0) for user_id, balance in rows}

    return [
        BalanceItemOut(
            family_id=family_id,
            user_id=user.id,
            display_name=user.display_name,
            role=membership.role,
            balance=balances_by_user.get(user.id, 0),
        )
        for membership, user in memberships_and_users
    ]


@router.get("/families/{family_id}/points/stats/{user_id}", response_model=ChildPointsStatsOut)
def get_points_stats(
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

    ledger_rows = (
        db.query(
            PointsLedger.created_at,
            PointsLedger.points_delta,
            PointsLedger.source_type,
        )
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .order_by(PointsLedger.created_at.asc())
        .all()
    )

    today = datetime.utcnow().date()
    activity_rows: list[tuple[date, int, PointsSourceEnum]] = []
    lifetime_earned_points = 0
    lifetime_spent_points = 0
    approved_tasks_count = 0
    first_activity_day: date | None = None

    for created_at, points_delta, source_type in ledger_rows:
        day_value = created_at.date()
        if first_activity_day is None:
            first_activity_day = day_value
        delta = int(points_delta or 0)
        activity_rows.append((day_value, delta, source_type))
        if _is_earned_points_delta(delta, source_type):
            lifetime_earned_points += delta
            if source_type == PointsSourceEnum.task_approval:
                approved_tasks_count += 1
        if _is_spent_points_delta(delta, source_type):
            lifetime_spent_points += abs(delta)

    if first_activity_day is None:
        first_activity_day = today

    active_days = max((today - first_activity_day).days + 1, 1)
    average_points_per_day = _safe_average(lifetime_earned_points, float(active_days))
    average_points_per_week = _safe_average(lifetime_earned_points, float(active_days) / 7.0)
    average_points_per_month = _safe_average(lifetime_earned_points, float(active_days) / 30.44)

    trends_daily = _build_day_trend(activity_rows, today, days=14)
    trends_weekly = _build_week_trend(activity_rows, today, weeks=12)
    trends_monthly = _build_month_trend(activity_rows, today, months=12)

    reward_request_rows = (
        db.query(
            RewardRedemption.reward_id,
            Reward.title,
            RewardRedemption.status,
            func.count(RewardRedemption.id),
        )
        .join(Reward, Reward.id == RewardRedemption.reward_id)
        .filter(Reward.family_id == family_id, RewardRedemption.requested_by_id == user_id)
        .group_by(RewardRedemption.reward_id, Reward.title, RewardRedemption.status)
        .all()
    )
    request_map: dict[int, dict[str, int | str]] = {}
    for reward_id, reward_title, redemption_status, count_value in reward_request_rows:
        entry = request_map.setdefault(
            int(reward_id),
            {
                "reward_title": str(reward_title),
                "request_count": 0,
                "approved_count": 0,
                "pending_count": 0,
                "rejected_count": 0,
            },
        )
        count_int = int(count_value or 0)
        entry["request_count"] = int(entry["request_count"]) + count_int
        if redemption_status == RedemptionStatusEnum.approved:
            entry["approved_count"] = int(entry["approved_count"]) + count_int
        elif redemption_status == RedemptionStatusEnum.pending:
            entry["pending_count"] = int(entry["pending_count"]) + count_int
        elif redemption_status == RedemptionStatusEnum.rejected:
            entry["rejected_count"] = int(entry["rejected_count"]) + count_int

    reward_request_stats = sorted(
        [
            PointsRewardRequestStatOut(
                reward_id=reward_id,
                reward_title=str(values["reward_title"]),
                request_count=int(values["request_count"]),
                approved_count=int(values["approved_count"]),
                pending_count=int(values["pending_count"]),
                rejected_count=int(values["rejected_count"]),
            )
            for reward_id, values in request_map.items()
        ],
        key=lambda item: (item.request_count, item.approved_count, item.reward_title.lower()),
        reverse=True,
    )

    reward_contribution_count = int(
        db.query(func.count(RewardContribution.id))
        .filter(RewardContribution.family_id == family_id, RewardContribution.user_id == user_id)
        .scalar()
        or 0
    )

    reward_delta_by_id: dict[int, int] = defaultdict(int)
    redemption_delta_rows = (
        db.query(RewardRedemption.reward_id, func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .join(
            PointsLedger,
            (PointsLedger.source_type == PointsSourceEnum.reward_redemption)
            & (PointsLedger.source_id == RewardRedemption.id),
        )
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .group_by(RewardRedemption.reward_id)
        .all()
    )
    for reward_id, net_delta in redemption_delta_rows:
        reward_delta_by_id[int(reward_id)] += int(net_delta or 0)

    contribution_delta_rows = (
        db.query(RewardContribution.reward_id, func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .join(
            PointsLedger,
            (PointsLedger.source_type == PointsSourceEnum.reward_contribution)
            & (PointsLedger.source_id == RewardContribution.id),
        )
        .filter(PointsLedger.family_id == family_id, PointsLedger.user_id == user_id)
        .group_by(RewardContribution.reward_id)
        .all()
    )
    for reward_id, net_delta in contribution_delta_rows:
        reward_delta_by_id[int(reward_id)] += int(net_delta or 0)

    reward_ids = list(reward_delta_by_id.keys())
    reward_titles: dict[int, str] = {}
    if reward_ids:
        title_rows = (
            db.query(Reward.id, Reward.title)
            .filter(Reward.family_id == family_id, Reward.id.in_(reward_ids))
            .all()
        )
        reward_titles = {int(reward_id): str(title) for reward_id, title in title_rows}

    spent_rows: list[tuple[int, int, str]] = []
    total_spent_for_share = 0
    for reward_id, net_delta in reward_delta_by_id.items():
        if net_delta >= 0:
            continue
        points_spent = abs(int(net_delta))
        total_spent_for_share += points_spent
        spent_rows.append((int(reward_id), points_spent, reward_titles.get(int(reward_id), f"Belohnung #{reward_id}")))

    reward_spent_stats = sorted(
        [
            PointsRewardSpendStatOut(
                reward_id=reward_id,
                reward_title=reward_title,
                points_spent=points_spent,
                share_percent=round((points_spent / total_spent_for_share) * 100.0, 2) if total_spent_for_share > 0 else 0.0,
            )
            for reward_id, points_spent, reward_title in spent_rows
        ],
        key=lambda item: (item.points_spent, item.reward_title.lower()),
        reverse=True,
    )

    return ChildPointsStatsOut(
        family_id=family_id,
        user_id=user_id,
        generated_at=datetime.utcnow(),
        current_points=get_points_balance(db, family_id, user_id),
        lifetime_earned_points=int(lifetime_earned_points),
        lifetime_spent_points=int(lifetime_spent_points),
        average_points_per_day=average_points_per_day,
        average_points_per_week=average_points_per_week,
        average_points_per_month=average_points_per_month,
        active_days=int(active_days),
        approved_tasks_count=int(approved_tasks_count),
        reward_requests_count=int(sum(item.request_count for item in reward_request_stats)),
        reward_contributions_count=reward_contribution_count,
        trends_daily=trends_daily,
        trends_weekly=trends_weekly,
        trends_monthly=trends_monthly,
        reward_request_stats=reward_request_stats,
        reward_spent_stats=reward_spent_stats,
    )


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
    user_names = _family_user_name_map(db, family_id)
    return _to_ledger_out(entry, user_names)
