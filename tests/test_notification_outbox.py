from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import notification_dispatcher as dispatcher
from app.database import Base
from app.migrations import run_migrations
from app.models import Family, LiveUpdateEvent, RemoteNotificationOutbox
from app.services import emit_live_event
from app.time_utils import utc_now_naive


class RemoteNotificationOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="hq-outbox-", suffix=".sqlite3")
        os.close(fd)
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.session_patch = patch.object(dispatcher, "SessionLocal", self.session_factory)
        self.session_patch.start()
        self.worker_id_patch = patch.object(dispatcher, "_worker_id", "test-worker")
        self.worker_id_patch.start()

    def tearDown(self) -> None:
        self.worker_id_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()
        Path(self.db_path).unlink(missing_ok=True)

    def _family(self) -> int:
        with self.session_factory() as db:
            family = Family(name="Outbox-Test")
            db.add(family)
            db.commit()
            return int(family.id)

    def test_creation_is_atomic_and_dispatch_flag_is_honored(self) -> None:
        family = self._family()
        db = self.session_factory()
        try:
            with patch("app.services.enqueue_remote_dispatch_job") as wake:
                event = emit_live_event(db, family, "task.created", {"task_id": 1})
                db.flush()
                self.assertEqual(db.query(RemoteNotificationOutbox).count(), 1)
                db.rollback()
                self.assertIsNone(db.get(LiveUpdateEvent, event.id))
                self.assertEqual(db.query(RemoteNotificationOutbox).count(), 0)
                wake.assert_not_called()

                emit_live_event(
                    db,
                    family,
                    "task.updated",
                    {"task_id": 2},
                    dispatch_notifications=False,
                )
                db.commit()
                wake.assert_not_called()
                self.assertEqual(db.query(RemoteNotificationOutbox).count(), 0)
        finally:
            db.close()

    def test_sqlite_migration_adds_outbox_to_existing_installation(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE families (id INTEGER PRIMARY KEY)"))

        run_migrations(engine)

        self.assertIn("remote_notification_outbox", inspect(engine).get_table_names())
        engine.dispose()

    def test_committed_outbox_survives_missing_wakeup_and_is_polled(self) -> None:
        family = self._family()
        db = self.session_factory()
        try:
            emit_live_event(db, family, "task.created", {"task_id": 7})
            db.commit()
        finally:
            db.close()

        self.assertFalse(
            dispatcher.enqueue_remote_dispatch_job(
                family_id=family,
                event_id=7,
                payload={"task_id": 7},
            )
        )
        job = dispatcher._claim_next_outbox_job()
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.payload, {"task_id": 7})
        with self.session_factory() as db:
            row = db.get(RemoteNotificationOutbox, job.outbox_id)
            self.assertEqual(row.status, dispatcher.OUTBOX_PROCESSING)
            self.assertEqual(row.attempt_count, 1)

    def test_success_deletes_only_after_dispatch_transaction(self) -> None:
        family = self._family()
        db = self.session_factory()
        event = LiveUpdateEvent(
            family_id=family,
            event_type="task.created",
            payload_json='{"task_id": 9}',
        )
        db.add(event)
        db.flush()
        outbox = RemoteNotificationOutbox(
            family_id=family,
            event_id=event.id,
            event_type=event.event_type,
            payload_json=event.payload_json,
        )
        db.add(outbox)
        db.commit()
        db.close()

        job = dispatcher._claim_next_outbox_job()
        self.assertIsNotNone(job)
        assert job is not None
        with patch.object(
            dispatcher,
            "dispatch_remote_pushes_for_event",
            return_value=SimpleNamespace(failed_count=0),
        ) as dispatch:
            dispatcher._process_job(job)
        dispatch.assert_called_once()
        with self.session_factory() as db:
            self.assertIsNone(db.get(RemoteNotificationOutbox, job.outbox_id))

    def test_failure_is_retried_and_poison_job_is_dead_lettered(self) -> None:
        family = self._family()
        db = self.session_factory()
        event = LiveUpdateEvent(family_id=family, event_type="task.created")
        db.add(event)
        db.flush()
        event_id = int(event.id)
        db.add(
            RemoteNotificationOutbox(
                family_id=family,
                event_id=event.id,
                event_type=event.event_type,
            )
        )
        db.commit()
        db.close()

        job = dispatcher._claim_next_outbox_job()
        self.assertIsNotNone(job)
        assert job is not None
        with patch.object(
            dispatcher,
            "dispatch_remote_pushes_for_event",
            side_effect=RuntimeError("provider unavailable"),
        ):
            dispatcher._process_job(job)
        with self.session_factory() as db:
            row = db.get(RemoteNotificationOutbox, job.outbox_id)
            self.assertEqual(row.status, dispatcher.OUTBOX_RETRY)
            self.assertIn("provider unavailable", row.last_error)
            self.assertGreater(row.available_at, utc_now_naive())

        with self.session_factory() as db:
            row = db.get(RemoteNotificationOutbox, job.outbox_id)
            row.status = dispatcher.OUTBOX_PROCESSING
            row.attempt_count = 1
            row.locked_by = "test-worker"
            row.locked_at = utc_now_naive()
            db.commit()
        with patch.object(dispatcher, "_MAX_ATTEMPTS", 1):
            with patch.object(
                dispatcher,
                "dispatch_remote_pushes_for_event",
                side_effect=RuntimeError("poison"),
            ):
                dispatcher._process_job(job)
        with self.session_factory() as db:
            row = db.get(RemoteNotificationOutbox, job.outbox_id)
            self.assertEqual(row.status, dispatcher.OUTBOX_DEAD)
            self.assertEqual(row.attempt_count, 1)

    def test_stale_claim_is_recovered_after_worker_restart(self) -> None:
        family = self._family()
        db = self.session_factory()
        event = LiveUpdateEvent(family_id=family, event_type="task.created")
        db.add(event)
        db.flush()
        event_id = int(event.id)
        db.add(
            RemoteNotificationOutbox(
                family_id=family,
                event_id=event.id,
                event_type=event.event_type,
                status=dispatcher.OUTBOX_PROCESSING,
                attempt_count=1,
                locked_at=utc_now_naive() - timedelta(seconds=dispatcher._LEASE_SECONDS + 1),
                locked_by="old-worker",
            )
        )
        db.commit()
        db.close()

        job = dispatcher._claim_next_outbox_job()
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.event_id, event_id)
        self.assertEqual(job.worker_id, "test-worker")


if __name__ == "__main__":
    unittest.main()
