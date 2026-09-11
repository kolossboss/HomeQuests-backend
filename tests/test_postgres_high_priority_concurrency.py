from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Family,
    FamilyMembership,
    RoleEnum,
    SpecialTaskIntervalEnum,
    SpecialTaskTemplate,
    Task,
    User,
)
from app.routers import families as family_router
from app.routers import tasks as task_router
from app.schemas import MemberUpdate
from app.security import hash_password


POSTGRES_URL = os.getenv("HOMEQUESTS_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(POSTGRES_URL, "HOMEQUESTS_TEST_POSTGRES_URL ist nicht gesetzt")
class PostgresHighPriorityConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = f"hq_concurrency_{uuid4().hex}"
        if re.fullmatch(r"[a-z0-9_]+", cls.schema) is None:
            raise RuntimeError("Ungültiger Test-Schemaname")
        cls.admin_engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
        with cls.admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(
            POSTGRES_URL,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={cls.schema}"},
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.session_factory = sessionmaker(
            bind=cls.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with cls.admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin_engine.dispose()

    def setUp(self) -> None:
        with self.session_factory() as db:
            for table in reversed(Base.metadata.sorted_tables):
                db.execute(table.delete())
            db.commit()

    def _fixture(self) -> tuple[int, int, int, int]:
        with self.session_factory() as db:
            family = Family(name="Postgres Concurrency")
            admin_one = User(display_name="Admin Eins", password_hash=hash_password("123"))
            admin_two = User(display_name="Admin Zwei", password_hash=hash_password("123"))
            child = User(display_name="Kind Eins", password_hash=hash_password("123"))
            db.add_all([family, admin_one, admin_two, child])
            db.flush()
            db.add_all(
                [
                    FamilyMembership(family_id=family.id, user_id=admin_one.id, role=RoleEnum.admin),
                    FamilyMembership(family_id=family.id, user_id=admin_two.id, role=RoleEnum.admin),
                    FamilyMembership(family_id=family.id, user_id=child.id, role=RoleEnum.child),
                ]
            )
            db.commit()
            return family.id, admin_one.id, admin_two.id, child.id

    def test_parallel_admin_demotions_leave_one_admin(self) -> None:
        family_id, admin_one_id, admin_two_id, _ = self._fixture()
        barrier = Barrier(2)

        def demote(user_id: int, display_name: str) -> str:
            with self.session_factory() as db:
                current_user = db.get(User, user_id)
                barrier.wait(timeout=5)
                try:
                    family_router.update_member(
                        family_id,
                        user_id,
                        MemberUpdate(display_name=display_name, role=RoleEnum.parent),
                        current_user=current_user,
                        db=db,
                    )
                    return "updated"
                except HTTPException as exc:
                    db.rollback()
                    return f"http-{exc.status_code}"

        with (
            patch.object(family_router, "emit_live_event"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(
                pool.map(
                    lambda args: demote(*args),
                    [(admin_one_id, "Admin Eins"), (admin_two_id, "Admin Zwei")],
                )
            )

        self.assertCountEqual(results, ["updated", "http-400"])
        with self.session_factory() as db:
            admin_count = (
                db.query(FamilyMembership)
                .filter_by(family_id=family_id, role=RoleEnum.admin)
                .count()
            )
        self.assertEqual(admin_count, 1)

    def test_parallel_special_claims_for_same_child_create_one_task(self) -> None:
        family_id, admin_one_id, _, child_id = self._fixture()
        with self.session_factory() as db:
            template = SpecialTaskTemplate(
                family_id=family_id,
                title="Einmal pro Woche",
                description=None,
                points=5,
                interval_type=SpecialTaskIntervalEnum.weekly,
                max_claims_per_interval=1,
                active_weekdays=[0, 1, 2, 3, 4, 5, 6],
                is_active=True,
                created_by_id=admin_one_id,
            )
            db.add(template)
            db.commit()
            template_id = template.id

        barrier = Barrier(2)

        def claim() -> str:
            with self.session_factory() as db:
                child = db.get(User, child_id)
                barrier.wait(timeout=5)
                try:
                    task_router.claim_special_task(template_id, current_user=child, db=db)
                    return "created"
                except HTTPException as exc:
                    db.rollback()
                    return f"http-{exc.status_code}"

        with (
            patch.object(task_router, "engine", self.engine),
            patch.object(task_router, "emit_live_event"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(pool.map(lambda _: claim(), range(2)))

        self.assertCountEqual(results, ["created", "http-400"])
        with self.session_factory() as db:
            task_count = (
                db.query(Task)
                .filter_by(special_template_id=template_id, assignee_id=child_id)
                .count()
            )
        self.assertEqual(task_count, 1)
