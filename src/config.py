from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram
    telegram_bot_token: str
    allowed_chats: list[int] = Field(default_factory=list)

    # Optional API keys
    twitter_bearer_token: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None

    # Cookies file for authenticated scraping (Facebook, Instagram)
    cookies_file: str | None = None

    # Performance
    max_file_size_mb: int = 50
    download_timeout_seconds: int = 30
    concurrent_downloads: int = 3

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/bot.log"

    # Debug mode — enables verbose per-step logging in scrapers
    debug_mode: bool = False


settings = Settings()  # type: ignore[call-arg]
