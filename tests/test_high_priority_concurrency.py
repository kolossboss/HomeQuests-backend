from __future__ import annotations

from datetime import datetime
import os
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Family,
    FamilyMembership,
    RoleEnum,
    SpecialTaskIntervalEnum,
    SpecialTaskTemplate,
    Task,
    TaskStatusEnum,
    User,
)
from app.routers import families as family_router
from app.routers import tasks as task_router
from app.schemas import MemberUpdate
from app.security import hash_password
from app.time_utils import app_local_now_naive


class HighPriorityConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="hq-high-priority-concurrency-", suffix=".sqlite3")
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
        os.unlink(self._db_path)

    def _fixture(self):
        db = self._session_factory()
        family = Family(name="Concurrency-Test")
        admin_one = User(display_name="Admin Eins", password_hash=hash_password("123"))
        admin_two = User(display_name="Admin Zwei", password_hash=hash_password("123"))
        child_one = User(display_name="Kind Eins", password_hash=hash_password("123"))
        child_two = User(display_name="Kind Zwei", password_hash=hash_password("123"))
        db.add_all([family, admin_one, admin_two, child_one, child_two])
        db.flush()
        db.add_all(
            [
                FamilyMembership(family_id=family.id, user_id=admin_one.id, role=RoleEnum.admin),
                FamilyMembership(family_id=family.id, user_id=admin_two.id, role=RoleEnum.admin),
                FamilyMembership(family_id=family.id, user_id=child_one.id, role=RoleEnum.child),
                FamilyMembership(family_id=family.id, user_id=child_two.id, role=RoleEnum.child),
            ]
        )
        template = SpecialTaskTemplate(
            family_id=family.id,
            title="Sonderaufgabe",
            description="Test",
            points=5,
            interval_type=SpecialTaskIntervalEnum.weekly,
            max_claims_per_interval=1,
            active_weekdays=[0, 1, 2, 3, 4, 5, 6],
            is_active=True,
            created_by_id=admin_one.id,
            created_at=app_local_now_naive(),
        )
        db.add(template)
        db.commit()
        return db, family, admin_one, admin_two, child_one, child_two, template

    def test_member_role_change_locks_family_before_last_admin_check(self) -> None:
        db, family, admin_one, admin_two, _child_one, _child_two, _template = self._fixture()
        try:
            payload = MemberUpdate(display_name="Admin Zwei", role=RoleEnum.parent)
            with (
                patch.object(family_router, "_get_family_for_update", wraps=family_router._get_family_for_update) as lock,
                patch.object(family_router, "emit_live_event"),
            ):
                family_router.update_member(
                    family.id,
                    admin_two.id,
                    payload,
                    current_user=admin_one,
                    db=db,
                )

            lock.assert_called_once_with(db, family.id)
            db.expire_all()
            last_admin_payload = MemberUpdate(display_name="Admin Eins", role=RoleEnum.parent)
            with self.assertRaises(HTTPException) as context:
                family_router.update_member(
                    family.id,
                    admin_one.id,
                    last_admin_payload,
                    current_user=admin_one,
                    db=db,
                )
            self.assertEqual(context.exception.status_code, 400)
            self.assertEqual(
                db.query(FamilyMembership)
                .filter(
                    FamilyMembership.family_id == family.id,
                    FamilyMembership.user_id == admin_one.id,
                    FamilyMembership.role == RoleEnum.admin,
                )
                .count(),
                1,
            )
        finally:
            db.close()

    def test_member_delete_locks_family_before_membership_delete(self) -> None:
        db, family, admin_one, admin_two, _child_one, _child_two, _template = self._fixture()
        try:
            with (
                patch.object(family_router, "_get_family_for_update", wraps=family_router._get_family_for_update) as lock,
                patch.object(family_router, "emit_live_event"),
            ):
                result = family_router.delete_member(
                    family.id,
                    admin_two.id,
                    current_user=admin_one,
                    db=db,
                )

            self.assertEqual(result, {"deleted": True})
            lock.assert_called_once_with(db, family.id)
            self.assertIsNone(
                db.query(FamilyMembership)
                .filter(
                    FamilyMembership.family_id == family.id,
                    FamilyMembership.user_id == admin_two.id,
                )
                .first()
            )
        finally:
            db.close()

    def test_special_task_usage_count_is_scoped_to_assignee(self) -> None:
        db, family, admin_one, _admin_two, child_one, child_two, template = self._fixture()
        now = datetime(2026, 8, 20, 12, 0, 0)
        try:
            db.add(
                Task(
                    family_id=family.id,
                    title="Bereits beansprucht",
                    description=None,
                    assignee_id=child_two.id,
                    points=template.points,
                    active_weekdays=[],
                    reminder_offsets_minutes=[],
                    recurrence_type="none",
                    special_template_id=template.id,
                    is_active=True,
                    status=TaskStatusEnum.open,
                    created_by_id=admin_one.id,
                    created_at=now,
                )
            )
            db.commit()
            with patch.object(task_router, "_task_now", return_value=now):
                self.assertEqual(
                    task_router._special_task_usage_count(
                        db,
                        template.id,
                        template.interval_type,
                        assignee_id=child_one.id,
                    ),
                    0,
                )
                self.assertEqual(
                    task_router._special_task_usage_count(
                        db,
                        template.id,
                        template.interval_type,
                        assignee_id=child_two.id,
                    ),
                    1,
                )
        finally:
            db.close()

    def test_special_task_claim_is_per_child_and_locked_before_check(self) -> None:
        db, family, _admin_one, _admin_two, child_one, child_two, template = self._fixture()
        now = datetime(2026, 8, 20, 12, 0, 0)
        try:
            with (
                patch.object(task_router, "engine", self._engine),
                patch.object(task_router, "_task_now", return_value=now),
                patch.object(task_router, "emit_live_event"),
                patch.object(
                    task_router,
                    "_lock_special_task_claim_window",
                    wraps=task_router._lock_special_task_claim_window,
                ) as claim_lock,
            ):
                first_claim = task_router.claim_special_task(
                    template.id,
                    current_user=child_one,
                    db=db,
                )
                self.assertEqual(first_claim.assignee_id, child_one.id)

                with self.assertRaises(HTTPException) as context:
                    task_router.claim_special_task(
                        template.id,
                        current_user=child_one,
                        db=db,
                    )
                self.assertEqual(context.exception.status_code, 400)
                db.rollback()

                second_claim = task_router.claim_special_task(
                    template.id,
                    current_user=child_two,
                    db=db,
                )
                self.assertEqual(second_claim.assignee_id, child_two.id)

            self.assertEqual(claim_lock.call_count, 3)
            self.assertEqual(claim_lock.call_args_list[0].args[1:], (template.id, child_one.id))
            self.assertEqual(claim_lock.call_args_list[1].args[1:], (template.id, child_one.id))
            self.assertEqual(claim_lock.call_args_list[2].args[1:], (template.id, child_two.id))
            self.assertEqual(
                db.query(Task)
                .filter(Task.special_template_id == template.id, Task.assignee_id == child_one.id)
                .count(),
                1,
            )
            self.assertEqual(
                db.query(Task)
                .filter(Task.special_template_id == template.id, Task.assignee_id == child_two.id)
                .count(),
                1,
            )
        finally:
            db.close()

    def test_special_task_claim_lock_uses_postgres_transaction_advisory_lock(self) -> None:
        fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        db = MagicMock()
        with patch.object(task_router, "engine", fake_engine):
            task_router._lock_special_task_claim_window(db, template_id=17, assignee_id=23)

        statement, params = db.execute.call_args.args
        self.assertIn("pg_advisory_xact_lock", str(statement))
        self.assertEqual(params, {"template_id": 17, "assignee_id": 23})


if __name__ == "__main__":
    unittest.main()
