from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Reminder Service"

    database_url: str = "sqlite:///./data/reminders.db"

    poll_interval_seconds: int = 30
    missed_grace_seconds: int = 6 * 3600

    default_snooze_days: int = 30

    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    telegram_bot_polling: bool = True

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
