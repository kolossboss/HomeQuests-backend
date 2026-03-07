from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HomeQuests API"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 30
    algorithm: str = "HS256"
    database_url: str = "postgresql+psycopg2://homequests:homequests@db:5432/homequests"
    cors_allow_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    auth_cookie_secure: bool = False
    sse_allow_query_token: bool = False
    penalty_worker_enabled: bool = True
    penalty_worker_interval_seconds: int = 60
    apns_enabled: bool = False
    apns_team_id: str | None = None
    apns_key_id: str | None = None
    apns_bundle_id: str | None = None
    apns_private_key: str | None = None
    apns_private_key_path: str | None = None
    secret_encryption_key: str | None = None
    push_worker_enabled: bool = True
    push_worker_interval_seconds: int = 60

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, value: str) -> str:
        secret = value.strip()
        if len(secret) < 16:
            raise ValueError("SECRET_KEY muss mindestens 16 Zeichen lang sein")
        return secret

    @field_validator("secret_encryption_key")
    @classmethod
    def validate_secret_encryption_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = value.strip()
        if not key:
            return None
        if len(key) < 16:
            raise ValueError("SECRET_ENCRYPTION_KEY muss mindestens 16 Zeichen lang sein")
        return key

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def parse_cors_allow_origins(cls, value):
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw == "*":
                return ["*"]
            return [entry.strip() for entry in raw.split(",") if entry.strip()]
        if isinstance(value, list):
            return [str(entry).strip() for entry in value if str(entry).strip()]
        return value

    @field_validator("penalty_worker_interval_seconds")
    @classmethod
    def validate_penalty_worker_interval_seconds(cls, value: int) -> int:
        if value < 15:
            raise ValueError("PENALTY_WORKER_INTERVAL_SECONDS muss mindestens 15 Sekunden sein")
        return value

    @field_validator("push_worker_interval_seconds")
    @classmethod
    def validate_push_worker_interval_seconds(cls, value: int) -> int:
        if value < 15:
            raise ValueError("PUSH_WORKER_INTERVAL_SECONDS muss mindestens 15 Sekunden sein")
        return value

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
