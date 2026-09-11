from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import notification_dispatcher as dispatcher
from app.database import Base
from app.models import Family, LiveUpdateEvent, RemoteNotificationOutbox


POSTGRES_URL = os.getenv("HOMEQUESTS_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(POSTGRES_URL, "HOMEQUESTS_TEST_POSTGRES_URL ist nicht gesetzt")
class PostgresNotificationOutboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = f"hq_outbox_{uuid4().hex}"
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

    def test_parallel_claims_skip_locked_row(self) -> None:
        with self.session_factory() as db:
            family = Family(name="Postgres Outbox")
            db.add(family)
            db.flush()
            event = LiveUpdateEvent(
                family_id=family.id,
                event_type="task.created",
                payload_json='{"task_id": 11}',
            )
            db.add(event)
            db.flush()
            db.add(
                RemoteNotificationOutbox(
                    family_id=family.id,
                    event_id=event.id,
                    event_type=event.event_type,
                    payload_json=event.payload_json,
                )
            )
            db.commit()

        barrier = Barrier(2)

        def claim() -> int | None:
            barrier.wait(timeout=5)
            job = dispatcher._claim_next_outbox_job()
            return job.outbox_id if job is not None else None

        with (
            patch.object(dispatcher, "SessionLocal", self.session_factory),
            patch.object(dispatcher, "_worker_id", "postgres-test-worker"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(pool.map(lambda _: claim(), range(2)))

        self.assertEqual(sum(result is not None for result in results), 1)
        with self.session_factory() as db:
            row = db.query(RemoteNotificationOutbox).one()
            self.assertEqual(row.status, dispatcher.OUTBOX_PROCESSING)
            self.assertEqual(row.attempt_count, 1)


if __name__ == "__main__":
    unittest.main()
