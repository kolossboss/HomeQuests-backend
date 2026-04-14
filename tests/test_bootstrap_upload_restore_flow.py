from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import UploadFile
from starlette.requests import Request

from app.database import Base
from app.db_tools import DbBackupFileInfo, DbRestoreResult
from app.models import User
from app.routers import auth
from app.schemas import BootstrapRestoreRequest, LoginRequest
from app.security import hash_password


class BootstrapUploadRestoreFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, db_path = tempfile.mkstemp(prefix="hq-bootstrap-test-", suffix=".sqlite3")
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

    @staticmethod
    def _make_request(path: str = "/auth/login") -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "scheme": "http",
            "query_string": b"",
            "client": ("testclient", 12345),
            "server": ("testserver", 80),
        }
        return Request(scope)

    @staticmethod
    def _upload_file(name: str, data: bytes) -> UploadFile:
        handle = SpooledTemporaryFile()
        handle.write(data)
        handle.seek(0)
        return UploadFile(file=handle, filename=name)

    def test_bootstrap_upload_restore_and_login_flow(self) -> None:
        db = self._session_factory()
        uploaded = DbBackupFileInfo(
            file_name="restore_seed_20260413_140000.dump",
            file_path="/tmp/homequests-backups/restore_seed_20260413_140000.dump",
            size_bytes=128,
            modified_at_utc=datetime.now(UTC),
        )

        def fake_restore_backup(*, backup_file: str, timeout_seconds: int | None = None) -> DbRestoreResult:
            self.assertEqual(backup_file, uploaded.file_path)
            restore_session = self._session_factory()
            try:
                restore_session.add(
                    User(
                        email="restore-admin@example.com",
                        display_name="RestoreAdmin",
                        password_hash=hash_password("123"),
                        is_active=True,
                    )
                )
                restore_session.commit()
            finally:
                restore_session.close()

            return DbRestoreResult(
                backup_file_path=backup_file,
                duration_seconds=0.123,
                restored_at_utc=datetime.now(UTC),
                database_engine="postgresql",
            )

        with (
            patch.object(auth, "engine", self._engine),
            patch.object(auth, "SessionLocal", self._session_factory),
            patch.object(auth, "store_uploaded_backup", return_value=uploaded) as store_mock,
            patch.object(auth, "restore_backup", side_effect=fake_restore_backup) as restore_mock,
        ):
            upload_result = auth.bootstrap_backup_upload(
                file=self._upload_file("seed.dump", b"dummy-backup"),
                target_dir="/tmp/homequests-backups",
                db=db,
            )
            self.assertTrue(upload_result.uploaded)
            self.assertEqual(upload_result.file_path, uploaded.file_path)
            store_mock.assert_called_once()

            restore_result = auth.bootstrap_restore(
                payload=BootstrapRestoreRequest(backup_file=uploaded.file_path),
                db=db,
            )
            self.assertTrue(restore_result.restored)
            self.assertEqual(restore_result.user_count, 1)
            restore_mock.assert_called_once()

        db.close()

        login_db = self._session_factory()
        try:
            token_response = auth.login(
                payload=LoginRequest(login="RestoreAdmin", password="123"),
                request=self._make_request(),
                response=Response(),
                db=login_db,
            )
        finally:
            login_db.close()

        self.assertTrue(token_response.access_token)

    def test_upload_is_blocked_after_bootstrap_completed(self) -> None:
        db = self._session_factory()
        try:
            db.add(
                User(
                    email="admin@example.com",
                    display_name="Admin",
                    password_hash=hash_password("123"),
                    is_active=True,
                )
            )
            db.commit()

            with self.assertRaises(HTTPException) as context:
                auth.bootstrap_backup_upload(
                    file=self._upload_file("seed.dump", b"dummy-backup"),
                    target_dir="/tmp/homequests-backups",
                    db=db,
                )
            self.assertEqual(context.exception.status_code, 400)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
