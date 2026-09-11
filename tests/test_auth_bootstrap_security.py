from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.config import Settings
from app.database import Base
from app.models import User
from app.routers import auth
from app.schemas import BootstrapRequest


class AuthBootstrapSecurityTests(unittest.TestCase):
    @staticmethod
    def _request(path: str = "/auth/bootstrap") -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [],
                "scheme": "http",
                "query_string": b"",
                "client": ("testclient", 12345),
                "server": ("testserver", 80),
            }
        )

    def _session(self):
        fd, db_path = tempfile.mkstemp(prefix="hq-auth-bootstrap-", suffix=".sqlite3")
        os.close(fd)
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        return engine, db_path, session_factory()

    def test_production_rejects_known_secret_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                environment="production",
                secret_key="CHANGE_THIS_SECRET",
                bootstrap_setup_token="configured-token-123",
            )

    def test_production_requires_bootstrap_setup_token(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                environment="production",
                secret_key="a-secure-production-secret-key-123456",
                bootstrap_setup_token=None,
            )

    def test_production_rejects_default_database_passwords(self) -> None:
        for database_url in (
            "postgresql+psycopg2://homequests:homequests@db:5432/homequests",
            "postgresql+psycopg2://homequests:PLEASE_CHANGE_DB_PASSWORD@db:5432/homequests",
        ):
            with self.assertRaises(ValueError):
                Settings(
                    _env_file=None,
                    environment="production",
                    secret_key="a-secure-production-secret-key-123456",
                    bootstrap_setup_token="a-secure-bootstrap-token-123456",
                    database_url=database_url,
                )

    def test_development_and_test_allow_documented_local_secret(self) -> None:
        for environment in ("development", "test"):
            config = Settings(
                _env_file=None,
                environment=environment,
                secret_key="change-me-in-development",
            )
            self.assertEqual(config.environment, environment)

    def test_setup_token_is_optional_and_constant_time_checked_when_configured(self) -> None:
        with patch.object(auth.settings, "bootstrap_setup_token", "configured-token-123"):
            with self.assertRaises(HTTPException) as missing:
                auth._require_setup_token(None)
            self.assertEqual(missing.exception.status_code, 401)

            with self.assertRaises(HTTPException) as context:
                auth._require_setup_token("wrong-token-123")
            self.assertEqual(context.exception.status_code, 401)

            with patch("app.routers.auth.compare_digest", return_value=True) as compare:
                auth._require_setup_token("candidate-token-123")
            compare.assert_called_once_with("candidate-token-123", "configured-token-123")

        with patch.object(auth.settings, "bootstrap_setup_token", None):
            auth._require_setup_token(None)

    def test_bootstrap_status_remains_public_when_setup_token_is_configured(self) -> None:
        engine, db_path, db = self._session()
        try:
            with patch.object(auth.settings, "bootstrap_setup_token", "configured-token-123"):
                result = auth.bootstrap_status(db=db)
            self.assertTrue(result.bootstrap_required)
            self.assertTrue(result.setup_token_required)
        finally:
            db.close()
            engine.dispose()
            os.unlink(db_path)

    def test_bootstrap_rejects_wrong_token_without_creating_user(self) -> None:
        engine, db_path, db = self._session()
        payload = BootstrapRequest(
            display_name="Admin",
            email="admin@example.com",
            password="123",
            password_confirm="123",
        )
        try:
            with patch.object(auth.settings, "bootstrap_setup_token", "configured-token-123"):
                with self.assertRaises(HTTPException) as context:
                    auth.bootstrap(
                        payload=payload,
                        request=self._request(),
                        response=Response(),
                        setup_token="wrong-token-123",
                        db=db,
                    )
            self.assertEqual(context.exception.status_code, 401)
            self.assertEqual(db.query(User).count(), 0)
        finally:
            db.close()
            engine.dispose()
            os.unlink(db_path)


if __name__ == "__main__":
    unittest.main()
