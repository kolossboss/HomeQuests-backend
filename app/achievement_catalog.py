from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .models import (
    AchievementDefinition,
    AchievementDifficultyEnum,
    AchievementRewardKindEnum,
    AchievementRuleKindEnum,
)


DEFAULT_REWARD_POINTS_BY_DIFFICULTY: dict[AchievementDifficultyEnum, int] = {
    AchievementDifficultyEnum.bronze: 10,
    AchievementDifficultyEnum.silver: 25,
    AchievementDifficultyEnum.gold: 50,
    AchievementDifficultyEnum.platinum: 150,
    AchievementDifficultyEnum.diamond: 300,
}


@dataclass(frozen=True)
class AchievementSeed:
    key: str
    name: str
    description: str
    category: str
    icon_key: str
    difficulty: AchievementDifficultyEnum
    rule_kind: AchievementRuleKindEnum
    rule_config: dict
    reward_kind: AchievementRewardKindEnum
    reward_config: dict
    teaser: str
    sort_order: int


def _reward_config(difficulty: AchievementDifficultyEnum) -> dict:
    points = DEFAULT_REWARD_POINTS_BY_DIFFICULTY[difficulty]
    return {
        "points": points,
        "label": f"{points} Bonuspunkte",
    }


def _aggregate_seed(
    *,
    key: str,
    name: str,
    description: str,
    category: str,
    icon_key: str,
    difficulty: AchievementDifficultyEnum,
    metric: str,
    target: int,
    teaser: str,
    sort_order: int,
    reward_points: int | None = None,
) -> AchievementSeed:
    rule_config = {"metric": metric, "target": target}
    return AchievementSeed(
        key=key,
        name=name,
        description=description,
        category=category,
        icon_key=icon_key,
        difficulty=difficulty,
        rule_kind=AchievementRuleKindEnum.aggregate_count,
        rule_config=rule_config,
        reward_kind=AchievementRewardKindEnum.points_grant,
        reward_config=_reward_config_for_points(reward_points) if reward_points is not None else _reward_config(difficulty),
        teaser=teaser,
        sort_order=sort_order,
    )


def _streak_seed(
    *,
    key: str,
    name: str,
    description: str,
    category: str,
    icon_key: str,
    difficulty: AchievementDifficultyEnum,
    period: str,
    metric: str,
    target: int,
    teaser: str,
    sort_order: int,
    recurrence_types: list[str] | None = None,
    completion_weekday_cutoff: int | None = None,
    completion_hour_cutoff: int | None = None,
    minimum_tasks: int = 1,
) -> AchievementSeed:
    rule_config = {
        "period": period,
        "metric": metric,
        "target": target,
        "minimum_tasks": minimum_tasks,
    }
    if recurrence_types:
        rule_config["recurrence_types"] = recurrence_types
    if completion_weekday_cutoff is not None:
        rule_config["completion_weekday_cutoff"] = completion_weekday_cutoff
    if completion_hour_cutoff is not None:
        rule_config["completion_hour_cutoff"] = completion_hour_cutoff

    return AchievementSeed(
        key=key,
        name=name,
        description=description,
        category=category,
        icon_key=icon_key,
        difficulty=difficulty,
        rule_kind=AchievementRuleKindEnum.streak,
        rule_config=rule_config,
        reward_kind=AchievementRewardKindEnum.points_grant,
        reward_config=_reward_config(difficulty),
        teaser=teaser,
        sort_order=sort_order,
    )


def _reward_config_for_points(points: int) -> dict:
    return {
        "points": int(points),
        "label": f"{int(points)} Bonuspunkte",
    }


def _point_collector_seeds() -> list[AchievementSeed]:
    tiers = [
        ("point_collector_bronze", "Punktesammler Bronze", AchievementDifficultyEnum.bronze, 500, 20, "coin-stack", 2010),
        ("point_collector_silver", "Punktesammler Silber", AchievementDifficultyEnum.silver, 1500, 50, "coin-stack", 2020),
        ("point_collector_silver_metallic", "Punktesammler Silber Metallic", AchievementDifficultyEnum.silver, 2000, 50, "coin-stack", 2030),
        ("point_collector_gold", "Punktesammler Gold", AchievementDifficultyEnum.gold, 3000, 100, "trophy", 2040),
        ("point_collector_gold_deluxe", "Punktesammler Gold Deluxe", AchievementDifficultyEnum.gold, 4000, 100, "trophy", 2050),
        ("point_collector_platinum", "Punktesammler Platin", AchievementDifficultyEnum.platinum, 5000, 150, "crown", 2060),
        ("point_collector_platinum_ultra", "Punktesammler Platin Ultra", AchievementDifficultyEnum.platinum, 6500, 150, "crown-check", 2070),
        ("point_collector_diamond", "Punktesammler Diamant", AchievementDifficultyEnum.diamond, 8000, 300, "diamond", 2080),
        ("point_collector_perfect_diamond", "Punktesammler Perfekt Diamant", AchievementDifficultyEnum.diamond, 10000, 300, "diamond", 2090),
    ]
    return [
        _aggregate_seed(
            key=key,
            name=name,
            description=f"Sammle insgesamt {target} jemals verdiente Punkte.",
            category="punkte",
            icon_key=icon_key,
            difficulty=difficulty,
            metric="earned_points_total",
            target=target,
            teaser=f"{target} jemals verdiente Punkte",
            sort_order=sort_order,
            reward_points=reward_points,
        )
        for key, name, difficulty, target, reward_points, icon_key, sort_order in tiers
    ]


def _treasure_chamber_seeds() -> list[AchievementSeed]:
    tiers = [
        ("treasure_chamber_bronze", "Schatzkammer Bronze", AchievementDifficultyEnum.bronze, 200, 20, "piggy-bank", 3010),
        ("treasure_chamber_silver", "Schatzkammer Silber", AchievementDifficultyEnum.silver, 800, 50, "vault", 3020),
        ("treasure_chamber_gold", "Schatzkammer Gold", AchievementDifficultyEnum.gold, 1500, 100, "vault", 3030),
        ("treasure_chamber_platinum", "Schatzkammer Platin", AchievementDifficultyEnum.platinum, 2000, 150, "bank", 3040),
        ("treasure_chamber_diamond", "Schatzkammer Diamant", AchievementDifficultyEnum.diamond, 5000, 300, "diamond", 3050),
    ]
    return [
        _aggregate_seed(
            key=key,
            name=name,
            description=f"Habe {target} Punkte gleichzeitig auf deinem Konto angespart.",
            category="sparen",
            icon_key=icon_key,
            difficulty=difficulty,
            metric="current_points_balance",
            target=target,
            teaser=f"{target} Punkte auf dem Konto",
            sort_order=sort_order,
            reward_points=reward_points,
        )
        for key, name, difficulty, target, reward_points, icon_key, sort_order in tiers
    ]


def _reward_redemption_seeds() -> list[AchievementSeed]:
    tiers = [
        ("reward_redeemer_bronze", "Wunsch-Einlöser Bronze", AchievementDifficultyEnum.bronze, 20, 10, "gift-open", 4010),
        ("reward_redeemer_silver", "Wunsch-Einlöser Silber", AchievementDifficultyEnum.silver, 50, 20, "gift-open", 4020),
        ("reward_redeemer_gold", "Wunsch-Einlöser Gold", AchievementDifficultyEnum.gold, 100, 50, "gift-spark", 4030),
        ("reward_redeemer_diamond", "Wunsch-Einlöser Diamant", AchievementDifficultyEnum.diamond, 200, 100, "diamond-gift", 4040),
    ]
    return [
        _aggregate_seed(
            key=key,
            name=name,
            description=f"Löse {target} von Eltern bestätigte Belohnungen ein.",
            category="belohnungen",
            icon_key=icon_key,
            difficulty=difficulty,
            metric="approved_reward_redemptions_total",
            target=target,
            teaser=f"{target} eingelöste Belohnungen",
            sort_order=sort_order,
            reward_points=reward_points,
        )
        for key, name, difficulty, target, reward_points, icon_key, sort_order in tiers
    ]


ACHIEVEMENT_CATALOG: list[AchievementSeed] = [
    _aggregate_seed(
        key="tasks_10",
        name="Anpacker",
        description="Lass 10 Aufgaben erfolgreich bestätigen.",
        category="aufgaben",
        icon_key="check-stack",
        difficulty=AchievementDifficultyEnum.bronze,
        metric="approved_tasks_total",
        target=10,
        teaser="10 bestätigte Aufgaben",
        sort_order=20,
    ),
    _aggregate_seed(
        key="weekly_5",
        name="Wochenstarter",
        description="Schließe 5 Wochenaufgaben erfolgreich ab.",
        category="wochenaufgaben",
        icon_key="calendar-week",
        difficulty=AchievementDifficultyEnum.bronze,
        metric="approved_weekly_tasks_total",
        target=5,
        teaser="5 bestätigte Wochenaufgaben",
        sort_order=30,
    ),
    _aggregate_seed(
        key="special_3",
        name="Hilfsmodus",
        description="Erledige 3 bestätigte Sonderaufgaben.",
        category="sonderaufgaben",
        icon_key="flash",
        difficulty=AchievementDifficultyEnum.bronze,
        metric="approved_special_tasks_total",
        target=3,
        teaser="3 bestätigte Sonderaufgaben",
        sort_order=40,
    ),
    _streak_seed(
        key="streak_2",
        name="Zuverlässig Bronze",
        description="Erledige 2 Wochen in Folge alle fälligen Aufgaben.",
        category="streak",
        icon_key="shield",
        difficulty=AchievementDifficultyEnum.bronze,
        period="week",
        metric="all_due_tasks_completed",
        target=2,
        teaser="2 vollständige Wochen in Folge",
        sort_order=50,
    ),
    _streak_seed(
        key="speedworker_1",
        name="Speedworker Bronze",
        description="Schließe eine Woche lang alle Wochenaufgaben bereits bis Dienstagabend ab.",
        category="tempo",
        icon_key="rocket",
        difficulty=AchievementDifficultyEnum.bronze,
        period="week",
        metric="all_due_tasks_completed_early",
        recurrence_types=["weekly"],
        completion_weekday_cutoff=1,
        completion_hour_cutoff=20,
        target=1,
        teaser="1 frühe Wochenserie",
        sort_order=60,
    ),
    _aggregate_seed(
        key="tasks_30",
        name="Dranbleiber",
        description="Lass 30 Aufgaben erfolgreich bestätigen.",
        category="aufgaben",
        icon_key="check-stack",
        difficulty=AchievementDifficultyEnum.silver,
        metric="approved_tasks_total",
        target=30,
        teaser="30 bestätigte Aufgaben",
        sort_order=120,
    ),
    _aggregate_seed(
        key="weekly_15",
        name="Wochenprofi",
        description="Schließe 15 Wochenaufgaben erfolgreich ab.",
        category="wochenaufgaben",
        icon_key="calendar-week",
        difficulty=AchievementDifficultyEnum.silver,
        metric="approved_weekly_tasks_total",
        target=15,
        teaser="15 bestätigte Wochenaufgaben",
        sort_order=130,
    ),
    _aggregate_seed(
        key="special_10",
        name="Extra-Einsatz",
        description="Erledige 10 bestätigte Sonderaufgaben.",
        category="sonderaufgaben",
        icon_key="flash",
        difficulty=AchievementDifficultyEnum.silver,
        metric="approved_special_tasks_total",
        target=10,
        teaser="10 bestätigte Sonderaufgaben",
        sort_order=140,
    ),
    _streak_seed(
        key="streak_4",
        name="Zuverlässig Silber",
        description="Erledige 4 Wochen in Folge alle fälligen Aufgaben.",
        category="streak",
        icon_key="shield",
        difficulty=AchievementDifficultyEnum.silver,
        period="week",
        metric="all_due_tasks_completed",
        target=4,
        teaser="4 vollständige Wochen in Folge",
        sort_order=150,
    ),
    _streak_seed(
        key="special_coverage_1",
        name="Monatsheld",
        description="Erledige in einem Monat jede aktive Sonderaufgabe mindestens einmal.",
        category="sonderaufgaben",
        icon_key="starburst",
        difficulty=AchievementDifficultyEnum.silver,
        period="month",
        metric="all_active_special_tasks_completed",
        target=1,
        teaser="Alle aktiven Sonderaufgaben in einem Monat",
        sort_order=160,
    ),
    _aggregate_seed(
        key="tasks_75",
        name="Aufgabenmaschine",
        description="Lass 75 Aufgaben erfolgreich bestätigen.",
        category="aufgaben",
        icon_key="gear-check",
        difficulty=AchievementDifficultyEnum.gold,
        metric="approved_tasks_total",
        target=75,
        teaser="75 bestätigte Aufgaben",
        sort_order=220,
    ),
    _aggregate_seed(
        key="weekly_30",
        name="Wochenroutine",
        description="Schließe 30 Wochenaufgaben erfolgreich ab.",
        category="wochenaufgaben",
        icon_key="calendar-week",
        difficulty=AchievementDifficultyEnum.gold,
        metric="approved_weekly_tasks_total",
        target=30,
        teaser="30 bestätigte Wochenaufgaben",
        sort_order=230,
    ),
    _aggregate_seed(
        key="special_25",
        name="Sondereinsatz Gold",
        description="Erledige 25 bestätigte Sonderaufgaben.",
        category="sonderaufgaben",
        icon_key="flash",
        difficulty=AchievementDifficultyEnum.gold,
        metric="approved_special_tasks_total",
        target=25,
        teaser="25 bestätigte Sonderaufgaben",
        sort_order=240,
    ),
    _streak_seed(
        key="streak_8",
        name="Zuverlässig Gold",
        description="Erledige 8 Wochen in Folge alle fälligen Aufgaben.",
        category="streak",
        icon_key="shield",
        difficulty=AchievementDifficultyEnum.gold,
        period="week",
        metric="all_due_tasks_completed",
        target=8,
        teaser="8 vollständige Wochen in Folge",
        sort_order=250,
    ),
    _streak_seed(
        key="speedworker_8",
        name="Speedworker Gold",
        description="Erledige 8 Wochen in Folge alle Wochenaufgaben bis Dienstagabend.",
        category="tempo",
        icon_key="rocket",
        difficulty=AchievementDifficultyEnum.gold,
        period="week",
        metric="all_due_tasks_completed_early",
        recurrence_types=["weekly"],
        completion_weekday_cutoff=1,
        completion_hour_cutoff=20,
        target=8,
        teaser="8 frühe Wochenserien",
        sort_order=260,
    ),
    _aggregate_seed(
        key="tasks_200",
        name="Hausheld",
        description="Lass 200 Aufgaben erfolgreich bestätigen.",
        category="aufgaben",
        icon_key="crown-check",
        difficulty=AchievementDifficultyEnum.platinum,
        metric="approved_tasks_total",
        target=200,
        teaser="200 bestätigte Aufgaben",
        sort_order=320,
    ),
    _streak_seed(
        key="streak_16",
        name="Zuverlässig Platin",
        description="Erledige 16 Wochen in Folge alle fälligen Aufgaben.",
        category="streak",
        icon_key="shield",
        difficulty=AchievementDifficultyEnum.platinum,
        period="week",
        metric="all_due_tasks_completed",
        target=16,
        teaser="16 vollständige Wochen in Folge",
        sort_order=330,
    ),
    _streak_seed(
        key="speedworker_16",
        name="Speedworker Platin",
        description="Erledige 16 Wochen in Folge alle Wochenaufgaben bis Dienstagabend.",
        category="tempo",
        icon_key="rocket",
        difficulty=AchievementDifficultyEnum.platinum,
        period="week",
        metric="all_due_tasks_completed_early",
        recurrence_types=["weekly"],
        completion_weekday_cutoff=1,
        completion_hour_cutoff=20,
        target=16,
        teaser="16 frühe Wochenserien",
        sort_order=340,
    ),
    _streak_seed(
        key="special_coverage_6",
        name="Sonderaufgaben-Meister",
        description="Erledige 6 Monate in Folge jede aktive Sonderaufgabe mindestens einmal.",
        category="sonderaufgaben",
        icon_key="starburst",
        difficulty=AchievementDifficultyEnum.platinum,
        period="month",
        metric="all_active_special_tasks_completed",
        target=6,
        teaser="6 Monate volle Sonderaufgaben-Abdeckung",
        sort_order=350,
    ),
    _streak_seed(
        key="monthly_master_6",
        name="Monatsmeister",
        description="Erledige 6 Monate in Folge alle fälligen Monatsaufgaben.",
        category="monatsaufgaben",
        icon_key="calendar-star",
        difficulty=AchievementDifficultyEnum.platinum,
        period="month",
        metric="all_due_tasks_completed",
        recurrence_types=["monthly"],
        target=6,
        teaser="6 vollständige Monatszyklen",
        sort_order=360,
    ),
] + _point_collector_seeds() + _treasure_chamber_seeds() + _reward_redemption_seeds() + [
    _aggregate_seed(
        key="tasks_500",
        name="Hausarbeits-Legende Diamant",
        description="Lass 500 Aufgaben erfolgreich bestätigen. Das ist ein Jahresprojekt, kein Wochenend-Sprint.",
        category="aufgaben",
        icon_key="diamond",
        difficulty=AchievementDifficultyEnum.diamond,
        metric="approved_tasks_total",
        target=500,
        teaser="500 bestätigte Aufgaben",
        sort_order=9100,
    ),
    _streak_seed(
        key="streak_52",
        name="Zuverlässig Diamant",
        description="Erledige 52 Wochen in Folge alle fälligen Aufgaben. Urlaubs-Freeze kann Serien fair pausieren.",
        category="streak",
        icon_key="diamond",
        difficulty=AchievementDifficultyEnum.diamond,
        period="week",
        metric="all_due_tasks_completed",
        target=52,
        teaser="52 vollständige Wochen in Folge",
        sort_order=9110,
    ),
    _streak_seed(
        key="speedworker_32",
        name="Speedworker Diamant",
        description="Erledige 32 Wochen in Folge alle Wochenaufgaben bis Dienstagabend.",
        category="tempo",
        icon_key="rocket",
        difficulty=AchievementDifficultyEnum.diamond,
        period="week",
        metric="all_due_tasks_completed_early",
        recurrence_types=["weekly"],
        completion_weekday_cutoff=1,
        completion_hour_cutoff=20,
        target=32,
        teaser="32 frühe Wochenserien",
        sort_order=9120,
    ),
]


def sync_achievement_catalog(db: Session) -> None:
    existing = {
        row.key: row
        for row in db.query(AchievementDefinition).all()
    }
    active_catalog_keys = {seed.key for seed in ACHIEVEMENT_CATALOG}

    for seed in ACHIEVEMENT_CATALOG:
        row = existing.get(seed.key)
        if row is None:
            row = AchievementDefinition(key=seed.key)
            db.add(row)

        row.name = seed.name
        row.description = seed.description
        row.category = seed.category
        row.icon_key = seed.icon_key
        row.sort_order = seed.sort_order
        row.difficulty = seed.difficulty
        row.rule_kind = seed.rule_kind
        row.rule_config = dict(seed.rule_config)
        row.reward_kind = seed.reward_kind
        row.reward_config = dict(seed.reward_config)
        row.teaser = seed.teaser
        row.is_active = True

    for key, row in existing.items():
        if _is_obsolete_managed_milestone_key(key, active_catalog_keys):
            row.is_active = False

    db.flush()


def _is_obsolete_managed_milestone_key(key: str, active_catalog_keys: set[str]) -> bool:
    if key in active_catalog_keys:
        return False
    if key in {"points_100", "points_250", "points_500", "points_1000"}:
        return True
    if key.startswith("point_collector_"):
        return True
    if key.startswith("points_") and key.endswith("_milestone"):
        return True
    if key.startswith("balance_") and key.removeprefix("balance_").isdigit():
        return True
    if key.startswith("treasure_chamber_"):
        return True
    return False
