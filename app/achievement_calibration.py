from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import (
    AchievementDefinition,
    AchievementFamilyCalibration,
    Family,
    PointsLedger,
    PointsSourceEnum,
    RecurrenceTypeEnum,
    Reward,
    SpecialTaskTemplate,
    Task,
)

CALIBRATION_MIN_DAYS = 14
CALIBRATION_MIN_TASKS = 10
CALIBRATION_MIN_REWARDS = 5
BASELINE_WEEKLY_POINTS = 250
MIN_POINT_SCALE = 35
MAX_POINT_SCALE = 800
HISTORICAL_SAMPLE_DAYS = 56
SCALABLE_POINT_METRICS = {"earned_points_total", "current_points_balance"}


@dataclass(frozen=True)
class CalibrationComputation:
    status: str
    started_at: datetime
    calibrated_at: datetime | None
    sample_days: int
    tasks_configured_count: int
    rewards_configured_count: int
    approved_tasks_sample_count: int
    approved_points_sample: int
    observed_weekly_points: int
    configured_weekly_points: int
    effective_weekly_points: int
    point_scale: int
    preview_payload: dict


def ensure_family_achievement_calibration(
    db: Session,
    family_id: int,
    *,
    now: datetime | None = None,
) -> AchievementFamilyCalibration:
    now = now or datetime.utcnow()
    calibration = _get_or_create_calibration(db, family_id, now)
    if calibration.status in {"ready", "applied"}:
        return calibration

    computation = compute_family_achievement_calibration(db, family_id, calibration.started_at, now=now)

    if calibration.status != "ready" and computation.status == "ready":
        calibration.calibrated_at = now
        calibration.status = "ready"
    elif calibration.status != "ready":
        calibration.status = computation.status

    _copy_computation_to_row(calibration, computation)
    db.flush()
    return calibration


def preview_family_achievement_calibration(db: Session, family_id: int, *, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    calibration = _get_or_create_calibration(db, family_id, now)
    current_payload = _calibration_payload(calibration)
    computation = compute_family_achievement_calibration(db, family_id, calibration.started_at, now=now, force_ready=True)
    return {
        "current": current_payload,
        "preview": _computation_payload(computation),
        "changes": _build_scaled_achievement_preview(
            db,
            current_scale=calibration.point_scale,
            preview_scale=computation.point_scale,
        ),
    }


def apply_family_achievement_recalibration(
    db: Session,
    family_id: int,
    *,
    now: datetime | None = None,
) -> AchievementFamilyCalibration:
    now = now or datetime.utcnow()
    calibration = _get_or_create_calibration(db, family_id, now)
    computation = compute_family_achievement_calibration(db, family_id, calibration.started_at, now=now, force_ready=True)
    calibration.status = "applied"
    calibration.calibrated_at = now
    _copy_computation_to_row(calibration, computation)
    calibration.preview_payload = dict(calibration.preview_payload or {})
    calibration.preview_payload["status"] = "applied"
    calibration.preview_payload["message"] = (
        f"Kalibrierung angewendet: ca. {calibration.effective_weekly_points} Punkte pro Woche, "
        f"Faktor {calibration.point_scale / 100:.2f}x."
    )
    db.flush()
    return calibration


def compute_family_achievement_calibration(
    db: Session,
    family_id: int,
    started_at: datetime,
    *,
    now: datetime | None = None,
    force_ready: bool = False,
) -> CalibrationComputation:
    now = now or datetime.utcnow()
    sample_start = max(started_at, now - timedelta(days=HISTORICAL_SAMPLE_DAYS))
    sample_days = max((now - started_at).days, 0)
    history_days = max((now - sample_start).days, 1)

    tasks_configured_count = _active_task_count(db, family_id)
    rewards_configured_count = _active_reward_count(db, family_id)
    approved_points_sample = _approved_points_sample(db, family_id, sample_start)
    approved_tasks_sample_count = _approved_tasks_sample_count(db, family_id, sample_start)
    observed_weekly_points = int(round((approved_points_sample / history_days) * 7)) if approved_points_sample > 0 else 0
    configured_weekly_points = _configured_weekly_points(db, family_id)
    effective_weekly_points = _effective_weekly_points(observed_weekly_points, configured_weekly_points)
    raw_scale = effective_weekly_points / BASELINE_WEEKLY_POINTS if effective_weekly_points > 0 else 1.0
    point_scale = _clamp(int(round(raw_scale * 100)), MIN_POINT_SCALE, MAX_POINT_SCALE)

    ready = (
        force_ready
        or (
            sample_days >= CALIBRATION_MIN_DAYS
            and tasks_configured_count >= CALIBRATION_MIN_TASKS
            and rewards_configured_count >= CALIBRATION_MIN_REWARDS
            and effective_weekly_points > 0
        )
    )
    status = "ready" if ready else "pending"
    preview_payload = _build_progress_payload(
        status=status,
        sample_days=sample_days,
        tasks_configured_count=tasks_configured_count,
        rewards_configured_count=rewards_configured_count,
        effective_weekly_points=effective_weekly_points,
        point_scale=point_scale,
    )
    return CalibrationComputation(
        status=status,
        started_at=started_at,
        calibrated_at=now if ready else None,
        sample_days=sample_days,
        tasks_configured_count=tasks_configured_count,
        rewards_configured_count=rewards_configured_count,
        approved_tasks_sample_count=approved_tasks_sample_count,
        approved_points_sample=approved_points_sample,
        observed_weekly_points=observed_weekly_points,
        configured_weekly_points=configured_weekly_points,
        effective_weekly_points=effective_weekly_points,
        point_scale=point_scale,
        preview_payload=preview_payload,
    )


def is_point_scaled_metric(metric: str) -> bool:
    return metric in SCALABLE_POINT_METRICS


def is_calibration_ready(calibration: AchievementFamilyCalibration | None) -> bool:
    return bool(calibration and calibration.status in {"ready", "applied"})


def is_calibration_applied(calibration: AchievementFamilyCalibration | None) -> bool:
    return bool(calibration and calibration.status == "applied")


def scaled_achievement_target(base_target: int, calibration: AchievementFamilyCalibration | None, metric: str) -> int:
    if not is_point_scaled_metric(metric) or not is_calibration_applied(calibration):
        return int(base_target)
    return max(_round_nice(int(base_target) * calibration.point_scale / 100), 1)


def scaled_achievement_reward(base_reward_points: int, calibration: AchievementFamilyCalibration | None, metric: str) -> int:
    if not is_point_scaled_metric(metric) or not is_calibration_applied(calibration):
        return int(base_reward_points)
    return max(_round_reward(int(base_reward_points) * calibration.point_scale / 100), 0)


def calibration_overview_payload(calibration: AchievementFamilyCalibration | None) -> dict:
    if calibration is None:
        return {
            "status": "pending",
            "message": "Kalibrierung wird vorbereitet.",
            "min_days_required": CALIBRATION_MIN_DAYS,
            "min_tasks_required": CALIBRATION_MIN_TASKS,
            "min_rewards_required": CALIBRATION_MIN_REWARDS,
        }
    return _calibration_payload(calibration)


def _get_or_create_calibration(db: Session, family_id: int, now: datetime) -> AchievementFamilyCalibration:
    calibration = (
        db.query(AchievementFamilyCalibration)
        .filter(AchievementFamilyCalibration.family_id == family_id)
        .first()
    )
    if calibration is not None:
        return calibration

    family = db.query(Family).filter(Family.id == family_id).first()
    started_at = family.created_at if family and family.created_at else now
    calibration = AchievementFamilyCalibration(
        family_id=family_id,
        status="pending",
        started_at=started_at,
        baseline_weekly_points=BASELINE_WEEKLY_POINTS,
        min_days_required=CALIBRATION_MIN_DAYS,
        min_tasks_required=CALIBRATION_MIN_TASKS,
        min_rewards_required=CALIBRATION_MIN_REWARDS,
        preview_payload={},
    )
    db.add(calibration)
    db.flush()
    return calibration


def _copy_computation_to_row(row: AchievementFamilyCalibration, computation: CalibrationComputation) -> None:
    row.sample_days = computation.sample_days
    row.tasks_configured_count = computation.tasks_configured_count
    row.rewards_configured_count = computation.rewards_configured_count
    row.approved_tasks_sample_count = computation.approved_tasks_sample_count
    row.approved_points_sample = computation.approved_points_sample
    row.observed_weekly_points = computation.observed_weekly_points
    row.configured_weekly_points = computation.configured_weekly_points
    row.effective_weekly_points = computation.effective_weekly_points
    row.point_scale = computation.point_scale
    row.preview_payload = dict(computation.preview_payload)


def _active_task_count(db: Session, family_id: int) -> int:
    return int(
        db.query(func.count(Task.id))
        .filter(Task.family_id == family_id, Task.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )


def _active_reward_count(db: Session, family_id: int) -> int:
    return int(
        db.query(func.count(Reward.id))
        .filter(Reward.family_id == family_id, Reward.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )


def _approved_points_sample(db: Session, family_id: int, sample_start: datetime) -> int:
    return int(
        db.query(func.coalesce(func.sum(PointsLedger.points_delta), 0))
        .filter(
            PointsLedger.family_id == family_id,
            PointsLedger.source_type == PointsSourceEnum.task_approval,
            PointsLedger.points_delta > 0,
            PointsLedger.created_at >= sample_start,
        )
        .scalar()
        or 0
    )


def _approved_tasks_sample_count(db: Session, family_id: int, sample_start: datetime) -> int:
    return int(
        db.query(func.count(PointsLedger.id))
        .filter(
            PointsLedger.family_id == family_id,
            PointsLedger.source_type == PointsSourceEnum.task_approval,
            PointsLedger.points_delta > 0,
            PointsLedger.created_at >= sample_start,
        )
        .scalar()
        or 0
    )


def _configured_weekly_points(db: Session, family_id: int) -> int:
    total = 0.0
    tasks = (
        db.query(Task)
        .filter(
            Task.family_id == family_id,
            Task.is_active == True,  # noqa: E712
            Task.special_template_id.is_(None),
        )
        .all()
    )
    for task in tasks:
        total += _weekly_points_for_recurrence(
            int(task.points or 0),
            str(task.recurrence_type or RecurrenceTypeEnum.none.value),
            task.active_weekdays or [],
        )

    templates = (
        db.query(SpecialTaskTemplate)
        .filter(
            SpecialTaskTemplate.family_id == family_id,
            SpecialTaskTemplate.is_active == True,  # noqa: E712
        )
        .all()
    )
    for template in templates:
        total += _weekly_points_for_recurrence(
            int(template.points or 0) * max(int(template.max_claims_per_interval or 1), 1),
            str(template.interval_type.value if hasattr(template.interval_type, "value") else template.interval_type),
            template.active_weekdays or [],
        )
    return int(round(total))


def _weekly_points_for_recurrence(points: int, recurrence: str, active_weekdays: list[int]) -> float:
    if points <= 0:
        return 0.0
    if recurrence == "daily":
        weekdays = len(active_weekdays) if active_weekdays else 7
        return float(points * max(min(weekdays, 7), 1))
    if recurrence == "weekly":
        return float(points)
    if recurrence == "monthly":
        return float(points) / 4.345
    return 0.0


def _effective_weekly_points(observed_weekly_points: int, configured_weekly_points: int) -> int:
    if observed_weekly_points > 0 and configured_weekly_points > 0:
        return int(round((observed_weekly_points * 0.7) + (configured_weekly_points * 0.3)))
    return max(observed_weekly_points, configured_weekly_points)


def _build_progress_payload(
    *,
    status: str,
    sample_days: int,
    tasks_configured_count: int,
    rewards_configured_count: int,
    effective_weekly_points: int,
    point_scale: int,
) -> dict:
    missing_days = max(CALIBRATION_MIN_DAYS - sample_days, 0)
    missing_tasks = max(CALIBRATION_MIN_TASKS - tasks_configured_count, 0)
    missing_rewards = max(CALIBRATION_MIN_REWARDS - rewards_configured_count, 0)
    if status == "ready":
        message = f"Kalibrierung bereit: ca. {effective_weekly_points} Punkte pro Woche, Faktor {point_scale / 100:.2f}x. Originalwerte bleiben aktiv, bis Eltern die Skalierung übernehmen."
    elif status == "applied":
        message = f"Kalibrierung angewendet: ca. {effective_weekly_points} Punkte pro Woche, Faktor {point_scale / 100:.2f}x."
    else:
        parts = []
        if missing_days:
            parts.append(f"noch ca. {missing_days} Tag(e)")
        if missing_tasks:
            parts.append(f"noch {missing_tasks} aktive Aufgabe(n)")
        if missing_rewards:
            parts.append(f"noch {missing_rewards} aktive Belohnung(en)")
        if effective_weekly_points <= 0:
            parts.append("noch Punktehistorie")
        message = "Kalibrierung läuft: " + (", ".join(parts) if parts else "Daten werden gesammelt.")
    return {
        "status": status,
        "message": message,
        "sample_days": sample_days,
        "min_days_required": CALIBRATION_MIN_DAYS,
        "tasks_configured_count": tasks_configured_count,
        "min_tasks_required": CALIBRATION_MIN_TASKS,
        "rewards_configured_count": rewards_configured_count,
        "min_rewards_required": CALIBRATION_MIN_REWARDS,
        "missing_days": missing_days,
        "missing_tasks": missing_tasks,
        "missing_rewards": missing_rewards,
        "effective_weekly_points": effective_weekly_points,
        "point_scale": point_scale,
        "point_scale_factor": round(point_scale / 100, 2),
    }


def _calibration_payload(calibration: AchievementFamilyCalibration) -> dict:
    payload = dict(calibration.preview_payload or {})
    if calibration.status == "ready":
        payload["message"] = (
            f"Kalibrierung bereit: ca. {calibration.effective_weekly_points} Punkte pro Woche, "
            f"Faktor {calibration.point_scale / 100:.2f}x. Originalwerte bleiben aktiv."
        )
    elif calibration.status == "applied":
        payload["message"] = (
            f"Kalibrierung angewendet: ca. {calibration.effective_weekly_points} Punkte pro Woche, "
            f"Faktor {calibration.point_scale / 100:.2f}x."
        )
    payload.update(
        {
            "family_id": calibration.family_id,
            "status": calibration.status,
            "started_at": calibration.started_at,
            "calibrated_at": calibration.calibrated_at,
            "baseline_weekly_points": calibration.baseline_weekly_points,
            "observed_weekly_points": calibration.observed_weekly_points,
            "configured_weekly_points": calibration.configured_weekly_points,
            "effective_weekly_points": calibration.effective_weekly_points,
            "point_scale": calibration.point_scale,
            "point_scale_factor": round(calibration.point_scale / 100, 2),
            "sample_days": calibration.sample_days,
            "approved_tasks_sample_count": calibration.approved_tasks_sample_count,
            "approved_points_sample": calibration.approved_points_sample,
            "updated_at": calibration.updated_at,
        }
    )
    return payload


def _computation_payload(computation: CalibrationComputation) -> dict:
    payload = dict(computation.preview_payload)
    payload.update(
        {
            "status": computation.status,
            "started_at": computation.started_at,
            "calibrated_at": computation.calibrated_at,
            "baseline_weekly_points": BASELINE_WEEKLY_POINTS,
            "observed_weekly_points": computation.observed_weekly_points,
            "configured_weekly_points": computation.configured_weekly_points,
            "effective_weekly_points": computation.effective_weekly_points,
            "point_scale": computation.point_scale,
            "point_scale_factor": round(computation.point_scale / 100, 2),
            "sample_days": computation.sample_days,
            "approved_tasks_sample_count": computation.approved_tasks_sample_count,
            "approved_points_sample": computation.approved_points_sample,
        }
    )
    return payload


def _build_scaled_achievement_preview(db: Session, *, current_scale: int, preview_scale: int) -> list[dict]:
    rows = (
        db.query(AchievementDefinition)
        .filter(AchievementDefinition.is_active == True)  # noqa: E712
        .order_by(AchievementDefinition.sort_order.asc(), AchievementDefinition.id.asc())
        .all()
    )
    changes: list[dict] = []
    for definition in rows:
        metric = str((definition.rule_config or {}).get("metric") or "")
        if not is_point_scaled_metric(metric):
            continue
        base_target = int((definition.rule_config or {}).get("target") or 0)
        base_reward = int((definition.reward_config or {}).get("points") or 0)
        current_target = _round_nice(base_target * current_scale / 100)
        preview_target = _round_nice(base_target * preview_scale / 100)
        current_reward = _round_reward(base_reward * current_scale / 100)
        preview_reward = _round_reward(base_reward * preview_scale / 100)
        changes.append(
            {
                "achievement_key": definition.key,
                "name": definition.name,
                "metric": metric,
                "current_target": current_target,
                "preview_target": preview_target,
                "current_reward_points": current_reward,
                "preview_reward_points": preview_reward,
            }
        )
    return changes


def _round_nice(value: float) -> int:
    if value <= 0:
        return 0
    step = 5
    if value >= 10000:
        step = 500
    elif value >= 1000:
        step = 100
    elif value >= 250:
        step = 50
    return max(int(round(value / step) * step), step)


def _round_reward(value: float) -> int:
    if value <= 0:
        return 0
    return max(int(round(value / 5) * 5), 5)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(min(value, maximum), minimum)
