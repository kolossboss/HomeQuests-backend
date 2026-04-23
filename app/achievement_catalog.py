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
) -> AchievementSeed:
    return AchievementSeed(
        key=key,
        name=name,
        description=description,
        category=category,
        icon_key=icon_key,
        difficulty=difficulty,
        rule_kind=AchievementRuleKindEnum.aggregate_count,
        rule_config={"metric": metric, "target": target},
        reward_kind=AchievementRewardKindEnum.points_grant,
        reward_config=_reward_config(difficulty),
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


ACHIEVEMENT_CATALOG: list[AchievementSeed] = [
    _aggregate_seed(
        key="points_100",
        name="Punktestarter",
        description="Sammle insgesamt 100 verdiente Punkte.",
        category="punkte",
        icon_key="spark",
        difficulty=AchievementDifficultyEnum.bronze,
        metric="earned_points_total",
        target=100,
        teaser="100 verdiente Punkte",
        sort_order=10,
    ),
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
        key="points_250",
        name="Punktesammler",
        description="Sammle insgesamt 250 verdiente Punkte.",
        category="punkte",
        icon_key="coin-stack",
        difficulty=AchievementDifficultyEnum.silver,
        metric="earned_points_total",
        target=250,
        teaser="250 verdiente Punkte",
        sort_order=110,
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
        key="points_500",
        name="500er Club",
        description="Sammle insgesamt 500 verdiente Punkte.",
        category="punkte",
        icon_key="trophy",
        difficulty=AchievementDifficultyEnum.gold,
        metric="earned_points_total",
        target=500,
        teaser="500 verdiente Punkte",
        sort_order=210,
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
        key="points_1000",
        name="Punktelegende",
        description="Sammle insgesamt 1000 verdiente Punkte.",
        category="punkte",
        icon_key="crown",
        difficulty=AchievementDifficultyEnum.platinum,
        metric="earned_points_total",
        target=1000,
        teaser="1000 verdiente Punkte",
        sort_order=310,
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
]


def sync_achievement_catalog(db: Session) -> None:
    existing = {
        row.key: row
        for row in db.query(AchievementDefinition).all()
    }

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

    db.flush()
