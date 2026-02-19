from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "HomeQuests API"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 365
    algorithm: str = "HS256"
    database_url: str = "postgresql+psycopg2://homequests:homequests@db:5432/homequests"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
