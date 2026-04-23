from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.achievement_engine import (
    build_achievement_overview,
    claim_achievement_profile,
    claim_achievement_reward,
    ensure_achievement_catalog,
    evaluate_achievements_for_user,
    record_task_outcome,
)
from app.database import Base
from app.models import (
    AchievementDefinition,
    AchievementFreezeScopeEnum,
    AchievementFreezeWindow,
    AchievementProgress,
    AchievementTaskOutcomeEnum,
    Family,
    PointsLedger,
    PointsSourceEnum,
    RoleEnum,
    SpecialTaskIntervalEnum,
    SpecialTaskTemplate,
    Task,
    User,
)
from app.security import hash_password


class AchievementEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="hq-achievements-test-", suffix=".sqlite3")
        os.close(fd)
        self._db_path = db_path
        self._engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self._engine)

    def tearDown(self) -> None:
        self._engine.dispose()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    def _create_family_and_user(self):
        db = self._session_factory()
        family = Family(name="Testfamilie")
        user = User(
            email="kind@example.com",
            display_name="Kind",
            password_hash=hash_password("123"),
            is_active=True,
        )
        db.add_all([family, user])
        db.commit()
        db.refresh(family)
        db.refresh(user)
        return db, family, user

    def test_points_achievement_unlock_requires_gift_claim_for_reward(self) -> None:
        db, family, user = self._create_family_and_user()
        try:
            ensure_achievement_catalog(db)
            db.add(
                PointsLedger(
                    family_id=family.id,
                    user_id=user.id,
                    source_type=PointsSourceEnum.task_approval,
                    source_id=1,
                    points_delta=500,
                    description="Viele Punkte",
                    created_by_id=user.id,
                )
            )
            db.flush()

            events = evaluate_achievements_for_user(db, family.id, user.id, triggered_by_id=user.id, emit_events=False)
            db.commit()

            unlocked_keys = {
                definition.key
                for definition in db.query(AchievementDefinition)
                .join(AchievementProgress, AchievementProgress.achievement_id == AchievementDefinition.id)
                .filter(
                    AchievementProgress.family_id == family.id,
                    AchievementProgress.user_id == user.id,
                    AchievementProgress.unlocked_at.is_not(None),
                )
                .all()
            }
            self.assertIn("points_500", unlocked_keys)
            self.assertTrue(any(event.reward_points == 50 for event in events))

            reward_rows_before = (
                db.query(PointsLedger)
                .filter(
                    PointsLedger.family_id == family.id,
                    PointsLedger.user_id == user.id,
                    PointsLedger.source_type == PointsSourceEnum.achievement_unlock,
                )
                .all()
            )
            self.assertEqual(reward_rows_before, [])

            points_500 = db.query(AchievementDefinition).filter(AchievementDefinition.key == "points_500").one()
            overview_unclaimed = build_achievement_overview(db, family.id, user.id)
            points_item = next(item for item in overview_unclaimed["items"] if item["key"] == "points_500")
            self.assertGreaterEqual(overview_unclaimed["unclaimed_count"], 1)
            self.assertTrue(points_item["is_profile_claimable"])
            self.assertFalse(points_item["is_reward_claimable"])

            claim_achievement_profile(db, family.id, user.id, points_500.id, triggered_by_id=user.id)
            overview_profile_claimed = build_achievement_overview(db, family.id, user.id)
            points_item = next(item for item in overview_profile_claimed["items"] if item["key"] == "points_500")
            self.assertGreaterEqual(overview_profile_claimed["reward_pending_count"], 1)
            self.assertFalse(points_item["is_profile_claimable"])
            self.assertTrue(points_item["is_reward_claimable"])

            _, points_delta = claim_achievement_reward(db, family.id, user.id, points_500.id, triggered_by_id=user.id)
            db.commit()
            self.assertEqual(points_delta, 50)
            overview_reward_claimed = build_achievement_overview(db, family.id, user.id)
            points_item = next(item for item in overview_reward_claimed["items"] if item["key"] == "points_500")
            self.assertEqual(overview_reward_claimed["reward_pending_count"], 0)
            self.assertFalse(points_item["is_profile_claimable"])
            self.assertFalse(points_item["is_reward_claimable"])

            reward_rows = (
                db.query(PointsLedger)
                .filter(
                    PointsLedger.family_id == family.id,
                    PointsLedger.user_id == user.id,
                    PointsLedger.source_type == PointsSourceEnum.achievement_unlock,
                )
                .all()
            )
            self.assertTrue(any(entry.points_delta == 50 for entry in reward_rows))
        finally:
            db.close()

    def test_weekly_streak_survives_freeze_gap(self) -> None:
        db, family, user = self._create_family_and_user()
        try:
            ensure_achievement_catalog(db)
            now = datetime.utcnow()
            current_week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
            previous_week_start = current_week_start - timedelta(days=7)
            older_week_start = current_week_start - timedelta(days=14)

            db.add(
                AchievementFreezeWindow(
                    family_id=family.id,
                    user_id=user.id,
                    scope=AchievementFreezeScopeEnum.streaks,
                    reason="Urlaub",
                    starts_at=previous_week_start,
                    ends_at=previous_week_start + timedelta(days=6, hours=23),
                    created_by_id=user.id,
                )
            )
            db.flush()

            first_task = Task(
                family_id=family.id,
                title="Aktuelle Woche",
                description=None,
                assignee_id=user.id,
                due_at=current_week_start + timedelta(days=2),
                points=10,
                reminder_offsets_minutes=[],
                active_weekdays=[],
                recurrence_type="weekly",
                always_submittable=False,
                penalty_enabled=False,
                penalty_points=0,
                special_template_id=None,
                is_active=True,
                status="approved",
                created_by_id=user.id,
            )
            second_task = Task(
                family_id=family.id,
                title="Vor zwei Wochen",
                description=None,
                assignee_id=user.id,
                due_at=older_week_start + timedelta(days=2),
                points=10,
                reminder_offsets_minutes=[],
                active_weekdays=[],
                recurrence_type="weekly",
                always_submittable=False,
                penalty_enabled=False,
                penalty_points=0,
                special_template_id=None,
                is_active=True,
                status="approved",
                created_by_id=user.id,
            )
            db.add_all([first_task, second_task])
            db.flush()

            record_task_outcome(
                db,
                first_task,
                outcome=AchievementTaskOutcomeEnum.approved,
                completed_at=current_week_start + timedelta(days=1),
                reviewed_at=current_week_start + timedelta(days=1, hours=1),
                points_awarded=10,
            )
            record_task_outcome(
                db,
                second_task,
                outcome=AchievementTaskOutcomeEnum.approved,
                completed_at=older_week_start + timedelta(days=1),
                reviewed_at=older_week_start + timedelta(days=1, hours=1),
                points_awarded=10,
            )

            evaluate_achievements_for_user(db, family.id, user.id, triggered_by_id=user.id, emit_events=False)
            db.commit()

            progress = (
                db.query(AchievementProgress)
                .join(AchievementDefinition, AchievementDefinition.id == AchievementProgress.achievement_id)
                .filter(
                    AchievementDefinition.key == "streak_2",
                    AchievementProgress.family_id == family.id,
                    AchievementProgress.user_id == user.id,
                )
                .first()
            )
            self.assertIsNotNone(progress)
            self.assertIsNotNone(progress.unlocked_at)
            self.assertGreaterEqual(progress.frozen_periods_used, 1)
        finally:
            db.close()

    def test_special_coverage_unlocks_when_all_templates_completed(self) -> None:
        db, family, user = self._create_family_and_user()
        try:
            ensure_achievement_catalog(db)
            template_a = SpecialTaskTemplate(
                family_id=family.id,
                title="Fenster",
                description=None,
                points=15,
                interval_type=SpecialTaskIntervalEnum.monthly,
                max_claims_per_interval=1,
                active_weekdays=[0, 1, 2, 3, 4, 5, 6],
                due_time_hhmm=None,
                is_active=True,
                created_by_id=user.id,
            )
            template_b = SpecialTaskTemplate(
                family_id=family.id,
                title="Keller",
                description=None,
                points=20,
                interval_type=SpecialTaskIntervalEnum.monthly,
                max_claims_per_interval=1,
                active_weekdays=[0, 1, 2, 3, 4, 5, 6],
                due_time_hhmm=None,
                is_active=True,
                created_by_id=user.id,
            )
            db.add_all([template_a, template_b])
            db.flush()

            task_a = Task(
                family_id=family.id,
                title="Fenster putzen",
                description=None,
                assignee_id=user.id,
                due_at=datetime.utcnow() - timedelta(days=2),
                points=15,
                reminder_offsets_minutes=[],
                active_weekdays=[],
                recurrence_type="none",
                always_submittable=False,
                penalty_enabled=False,
                penalty_points=0,
                special_template_id=template_a.id,
                is_active=True,
                status="approved",
                created_by_id=user.id,
            )
            task_b = Task(
                family_id=family.id,
                title="Keller aufräumen",
                description=None,
                assignee_id=user.id,
                due_at=datetime.utcnow() - timedelta(days=1),
                points=20,
                reminder_offsets_minutes=[],
                active_weekdays=[],
                recurrence_type="none",
                always_submittable=False,
                penalty_enabled=False,
                penalty_points=0,
                special_template_id=template_b.id,
                is_active=True,
                status="approved",
                created_by_id=user.id,
            )
            db.add_all([task_a, task_b])
            db.flush()

            record_task_outcome(
                db,
                task_a,
                outcome=AchievementTaskOutcomeEnum.approved,
                completed_at=datetime.utcnow() - timedelta(days=2),
                reviewed_at=datetime.utcnow() - timedelta(days=2),
                points_awarded=15,
            )
            record_task_outcome(
                db,
                task_b,
                outcome=AchievementTaskOutcomeEnum.approved,
                completed_at=datetime.utcnow() - timedelta(days=1),
                reviewed_at=datetime.utcnow() - timedelta(days=1),
                points_awarded=20,
            )

            evaluate_achievements_for_user(db, family.id, user.id, triggered_by_id=user.id, emit_events=False)
            db.commit()

            progress = (
                db.query(AchievementProgress)
                .join(AchievementDefinition, AchievementDefinition.id == AchievementProgress.achievement_id)
                .filter(
                    AchievementDefinition.key == "special_coverage_1",
                    AchievementProgress.family_id == family.id,
                    AchievementProgress.user_id == user.id,
                )
                .first()
            )
            self.assertIsNotNone(progress)
            self.assertIsNotNone(progress.unlocked_at)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
