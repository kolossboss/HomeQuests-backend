from __future__ import annotations

import asyncio
from datetime import date
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.database import Base
from app.db_tools import DbToolsError, create_backup, list_backup_files, resolve_backup_file_path
from app.live_bus import LiveEventBus
from app.login_limiter import LoginRateLimiter
from app.models import (
    Family,
    FamilyMembership,
    PointsLedger,
    PointsSourceEnum,
    RecurrenceTypeEnum,
    RedemptionStatusEnum,
    Reward,
    RewardRedemption,
    RoleEnum,
    Task,
    TaskStatusEnum,
    User,
)
from app.push_notifications import _sanitize_error_reason
from app.routers.live import _event_payload_for_user, _stream_membership_active
from app.routers.points import _build_month_trend
from app.routers.rewards import delete_reward, update_reward
from app.routers.tasks import submit_and_approve_task
from app.schemas import (
    HomeAssistantSettingsUpdateRequest,
    HomeAssistantUserConfigUpdateRequest,
    RewardUpdate,
    TaskSubmitAndApproveRequest,
)
from app.security import hash_password
from app.services import emit_live_event
from app.time_utils import app_local_now_naive


class SecurityStabilityTests(unittest.TestCase):
    def test_comma_separated_list_settings_start_from_compose_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORS_ALLOW_ORIGINS": "https://one.example,https://two.example",
                "DB_BACKUP_ALLOWED_DIRS": "/data/backups,/mnt/archive",
                "DB_BACKUP_DEFAULT_DIR": "/data/backups",
            },
            clear=False,
        ):
            config = Settings(_env_file=None)

        self.assertEqual(
            config.cors_allow_origins,
            ["https://one.example", "https://two.example"],
        )
        self.assertEqual(config.db_backup_allowed_dirs, ["/data/backups", "/mnt/archive"])

    def test_monthly_points_trend_does_not_double_earned_points(self) -> None:
        trend = _build_month_trend(
            [(date(2026, 7, 15), 12, PointsSourceEnum.task_approval)],
            date(2026, 7, 15),
            months=1,
        )
        self.assertEqual(trend[0].earned_points, 12)
        self.assertEqual(trend[0].net_points, 12)

    def test_live_and_push_signals_are_only_emitted_after_commit(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = session_factory()
        try:
            family = Family(name="Commit-Test")
            db.add(family)
            db.commit()
            with (
                patch("app.services.live_event_bus.publish") as publish,
                patch("app.services.enqueue_remote_dispatch_job", return_value=True) as enqueue,
            ):
                emit_live_event(db, family.id, "task.created", {"task_id": 1})
                publish.assert_not_called()
                enqueue.assert_not_called()
                db.rollback()
                publish.assert_not_called()
                enqueue.assert_not_called()

                emit_live_event(db, family.id, "task.created", {"task_id": 2})
                db.commit()
                publish.assert_called_once_with(family.id)
                enqueue.assert_called_once()
        finally:
            db.close()
            engine.dispose()

    def test_child_live_events_are_scoped(self) -> None:
        self.assertIsNone(
            _event_payload_for_user(
                "task.submitted",
                {"task_id": 10, "assignee_id": 99, "title": "Fremd"},
                user_id=7,
                role=RoleEnum.child,
            )
        )
        own = _event_payload_for_user(
            "task.submitted",
            {"task_id": 11, "assignee_id": 7, "title": "Eigen"},
            user_id=7,
            role=RoleEnum.child,
        )
        self.assertEqual(own["task_id"], 11)
        self.assertIsNone(
            _event_payload_for_user(
                "system.db.backup_created",
                {"file_path": "/secret/path.dump"},
                user_id=7,
                role=RoleEnum.child,
            )
        )

    def test_live_stream_access_reflects_user_deactivation(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = session_factory()
        try:
            family = Family(name="SSE-Test")
            user = User(display_name="SSE Kind", password_hash=hash_password("123"))
            db.add_all([family, user])
            db.flush()
            db.add(FamilyMembership(family_id=family.id, user_id=user.id, role=RoleEnum.child))
            db.commit()

            self.assertTrue(
                _stream_membership_active(db, family_id=family.id, user_id=user.id)
            )
            user.is_active = False
            db.commit()
            self.assertFalse(
                _stream_membership_active(db, family_id=family.id, user_id=user.id)
            )
        finally:
            db.close()
            engine.dispose()

    def test_notification_error_redaction(self) -> None:
        raw = "POST https://api.push.apple.com/3/device/ABCDEF123 token=secret Authorization:BearerSecret"
        sanitized = _sanitize_error_reason(raw)
        self.assertNotIn("ABCDEF123", sanitized)
        self.assertNotIn("token=secret", sanitized)
        self.assertNotIn("BearerSecret", sanitized)

    def test_home_assistant_inputs_are_normalized_and_validated(self) -> None:
        config = HomeAssistantSettingsUpdateRequest(ha_base_url="https://ha.local/", ha_token=" token ")
        self.assertEqual(config.ha_base_url, "https://ha.local")
        self.assertEqual(config.ha_token, "token")
        user = HomeAssistantUserConfigUpdateRequest(ha_notify_service="notify.mobile_app_iphone")
        self.assertEqual(user.ha_notify_service, "mobile_app_iphone")
        with self.assertRaises(ValidationError):
            HomeAssistantSettingsUpdateRequest(ha_base_url="file:///etc/passwd")
        with self.assertRaises(ValidationError):
            HomeAssistantUserConfigUpdateRequest(ha_notify_service="mobile/app")

    def test_login_limiter_blocks_and_can_be_cleared(self) -> None:
        limiter = LoginRateLimiter()
        key = limiter.key("127.0.0.1", "Kind")
        old_attempts = settings.login_rate_limit_attempts
        old_block = settings.login_rate_limit_block_seconds
        try:
            settings.login_rate_limit_attempts = 3
            settings.login_rate_limit_block_seconds = 30
            self.assertEqual(limiter.record_failure(key), 0)
            self.assertEqual(limiter.record_failure(key), 0)
            self.assertGreater(limiter.record_failure(key), 0)
            self.assertGreater(limiter.retry_after(key), 0)
            limiter.clear(key)
            self.assertEqual(limiter.retry_after(key), 0)
        finally:
            settings.login_rate_limit_attempts = old_attempts
            settings.login_rate_limit_block_seconds = old_block

    def test_nested_backups_are_listed(self) -> None:
        old_allowed = settings.db_backup_allowed_dirs
        old_default = settings.db_backup_default_dir
        with tempfile.TemporaryDirectory(prefix="hq-backup-list-") as tmp:
            settings.db_backup_allowed_dirs = [tmp]
            settings.db_backup_default_dir = tmp
            nested = Path(tmp, "year", "month")
            nested.mkdir(parents=True)
            backup = nested / "nested.dump"
            backup.write_bytes(b"backup")
            outside = nested / "ignored.txt"
            outside.write_text("ignored", encoding="utf-8")
            try:
                files = list_backup_files()
                self.assertEqual([entry.file_path for entry in files], [str(backup.resolve())])
            finally:
                settings.db_backup_allowed_dirs = old_allowed
                settings.db_backup_default_dir = old_default

    def test_relative_backup_name_is_resolved_inside_allowed_directory(self) -> None:
        old_allowed = settings.db_backup_allowed_dirs
        old_default = settings.db_backup_default_dir
        with tempfile.TemporaryDirectory(prefix="hq-backup-relative-") as tmp:
            settings.db_backup_allowed_dirs = [tmp]
            settings.db_backup_default_dir = tmp
            backup = Path(tmp, "relative.dump")
            backup.write_bytes(b"backup")
            try:
                self.assertEqual(resolve_backup_file_path("relative.dump"), backup.resolve())
            finally:
                settings.db_backup_allowed_dirs = old_allowed
                settings.db_backup_default_dir = old_default

    def test_backup_is_published_atomically_and_partial_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hq-backup-create-") as tmp:
            target = Path(tmp)

            def fake_run(cmd, **_kwargs):
                output = Path(cmd[cmd.index("--file") + 1])
                self.assertTrue(output.name.endswith(".partial"))
                output.write_bytes(b"valid-dump")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                patch("app.db_tools.backup_supported", return_value=True),
                patch("app.db_tools.pg_dump_available", return_value=True),
                patch("app.db_tools._pg_connection_parts", return_value=("db", 5432, "user", "pw", "db")),
                patch("app.db_tools.resolve_backup_target_dir", return_value=target),
                patch("app.db_tools.subprocess.run", side_effect=fake_run),
            ):
                result = create_backup(target_dir=str(target), filename_prefix="safe")
            self.assertTrue(Path(result.file_path).is_file())
            self.assertEqual(Path(result.file_path).stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(target.glob("*.partial")), [])

    def test_failed_backup_leaves_no_visible_dump(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hq-backup-fail-") as tmp:
            target = Path(tmp)

            def fake_run(cmd, **_kwargs):
                Path(cmd[cmd.index("--file") + 1]).write_bytes(b"partial")
                return SimpleNamespace(returncode=1, stderr="connection failed")

            with (
                patch("app.db_tools.backup_supported", return_value=True),
                patch("app.db_tools.pg_dump_available", return_value=True),
                patch("app.db_tools._pg_connection_parts", return_value=("db", 5432, "user", "pw", "db")),
                patch("app.db_tools.resolve_backup_target_dir", return_value=target),
                patch("app.db_tools.subprocess.run", side_effect=fake_run),
            ):
                with self.assertRaises(DbToolsError):
                    create_backup(target_dir=str(target), filename_prefix="safe")
            self.assertEqual(list(target.iterdir()), [])


class LiveEventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiter_is_woken_only_for_its_family(self) -> None:
        bus = LiveEventBus()
        waiter = asyncio.create_task(bus.wait_for_update(7, 0, 1.0))
        await asyncio.sleep(0)
        bus.publish(8)
        await asyncio.sleep(0.01)
        self.assertFalse(waiter.done())
        bus.publish(7)
        self.assertEqual(await waiter, 1)


class RewardIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="hq-reward-integrity-", suffix=".sqlite3")
        os.close(fd)
        self._db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self._engine)

    def tearDown(self) -> None:
        self._engine.dispose()
        os.unlink(self._db_path)

    def _fixture(self):
        db = self._session_factory()
        family = Family(name="Familie")
        admin = User(display_name="Admin", password_hash=hash_password("123"))
        child = User(display_name="Kind", password_hash=hash_password("123"))
        db.add_all([family, admin, child])
        db.flush()
        db.add_all(
            [
                FamilyMembership(family_id=family.id, user_id=admin.id, role=RoleEnum.admin),
                FamilyMembership(family_id=family.id, user_id=child.id, role=RoleEnum.child),
            ]
        )
        reward = Reward(
            family_id=family.id,
            title="Historisch",
            cost_points=20,
            is_shareable=False,
            is_active=True,
            created_by_id=admin.id,
        )
        db.add(reward)
        db.flush()
        db.add(RewardRedemption(reward_id=reward.id, requested_by_id=child.id, status=RedemptionStatusEnum.pending))
        db.commit()
        return db, admin, reward

    def test_reward_with_history_cannot_be_deleted(self) -> None:
        db, admin, reward = self._fixture()
        try:
            with self.assertRaises(HTTPException) as context:
                delete_reward(reward.id, current_user=admin, db=db)
            self.assertEqual(context.exception.status_code, 409)
            self.assertIsNotNone(db.query(Reward).filter(Reward.id == reward.id).first())
        finally:
            db.close()

    def test_active_redemption_blocks_cost_change_but_not_title_change(self) -> None:
        db, admin, reward = self._fixture()
        try:
            with self.assertRaises(HTTPException) as context:
                update_reward(
                    reward.id,
                    RewardUpdate(title="Neu", cost_points=30, is_shareable=False, is_active=True),
                    current_user=admin,
                    db=db,
                )
            self.assertEqual(context.exception.status_code, 409)
            db.rollback()
            updated = update_reward(
                reward.id,
                RewardUpdate(title="Neuer Titel", cost_points=20, is_shareable=False, is_active=True),
                current_user=admin,
                db=db,
            )
            self.assertEqual(updated.title, "Neuer Titel")
        finally:
            db.close()


class TaskAtomicWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="hq-task-atomic-", suffix=".sqlite3")
        os.close(fd)
        self._db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self._engine)

    def tearDown(self) -> None:
        self._engine.dispose()
        os.unlink(self._db_path)

    def test_submit_and_approve_is_atomic_and_cannot_book_twice(self) -> None:
        db = self._session_factory()
        try:
            family = Family(name="Familie")
            admin = User(display_name="Admin", password_hash=hash_password("123"))
            child = User(display_name="Kind", password_hash=hash_password("123"))
            db.add_all([family, admin, child])
            db.flush()
            db.add_all(
                [
                    FamilyMembership(family_id=family.id, user_id=admin.id, role=RoleEnum.admin),
                    FamilyMembership(family_id=family.id, user_id=child.id, role=RoleEnum.child),
                ]
            )
            task = Task(
                family_id=family.id,
                title="Direkt bestätigen",
                assignee_id=child.id,
                due_at=app_local_now_naive(),
                points=7,
                recurrence_type=RecurrenceTypeEnum.none.value,
                status=TaskStatusEnum.open,
                created_by_id=admin.id,
            )
            db.add(task)
            db.commit()

            result = submit_and_approve_task(
                task.id,
                TaskSubmitAndApproveRequest(note="erledigt", comment="ok"),
                current_user=admin,
                db=db,
            )
            self.assertEqual(result.status, TaskStatusEnum.approved)
            self.assertEqual(db.query(PointsLedger).filter(PointsLedger.user_id == child.id).count(), 1)

            with self.assertRaises(HTTPException) as context:
                submit_and_approve_task(
                    task.id,
                    TaskSubmitAndApproveRequest(),
                    current_user=admin,
                    db=db,
                )
            self.assertEqual(context.exception.status_code, 400)
            self.assertEqual(db.query(PointsLedger).filter(PointsLedger.user_id == child.id).count(), 1)
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
