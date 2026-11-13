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

    # Общий секрет сервиса и воркеров. Пусто — внутреннее API без проверки.
    internal_token: str | None = None
    # На сколько воркер арендует задачу. Просроченная аренда возвращается в очередь.
    task_lease_seconds: int = 60
    max_delivery_attempts: int = 5

    # Нужны процессу-воркеру. Сервис их не использует для отправки.
    service_url: str = "http://127.0.0.1:8000"
    worker_id: str | None = None
    worker_poll_seconds: float = 1

    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
