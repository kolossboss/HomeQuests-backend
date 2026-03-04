from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HomeQuests API"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 30
    algorithm: str = "HS256"
    database_url: str = "postgresql+psycopg2://homequests:homequests@db:5432/homequests"
    cors_allow_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
