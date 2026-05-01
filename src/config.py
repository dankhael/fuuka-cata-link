from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE_NAME: str = os.environ.get("ENV_FILE", ".env")
ENV_FILE_PATH: Path = Path(ENV_FILE_NAME).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE_NAME,
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

    # Cookies file for authenticated scraping (Facebook, Instagram, YouTube)
    cookies_file: str | None = None
    cookies_from_browser: str | None = None  # e.g. "chrome", "firefox", "edge"

    # yt-dlp JS runtime for YouTube (required since yt-dlp 2025+)
    ytdlp_js_runtime: str | None = None  # e.g. "deno", "nodejs", "deno:/path/to/deno"

    # Performance
    max_file_size_mb: int = 50
    auto_download_limit_mb: int = 10  # Compress media above this to ensure Telegram auto-downloads
    download_timeout_seconds: int = 30
    concurrent_downloads: int = 3

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/bot.log"

    # Diagnostics
    error_log_file: str = "logs/errors.log"
    performance_log_file: str = "logs/performance.log"
    diagnostics_max_size_mb: int = 5

    # Debug mode — enables verbose per-step logging in scrapers
    debug_mode: bool = False


settings = Settings()  # type: ignore[call-arg]


def _read_token_from_env_file(path: Path) -> str | None:
    """Return TELEGRAM_BOT_TOKEN as written in *path*, or None if missing/unreadable.

    Used to tell whether the active token came from the .env file or was overridden
    by an OS env var — pydantic-settings prefers OS env vars, which silently shadows
    the file and makes "wrong bot is running" bugs hard to spot.
    """
    if not path.is_file():
        return None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if sep and key.strip().upper() == "TELEGRAM_BOT_TOKEN":
                return value.strip().strip("'\"")
    except OSError:
        return None
    return None


def env_diagnostics() -> dict[str, object]:
    """Snapshot of where the active settings actually came from."""
    file_token = _read_token_from_env_file(ENV_FILE_PATH)
    os_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    active = settings.telegram_bot_token

    if os_token and os_token == active:
        source = "os_env"
    elif file_token and file_token == active:
        source = "env_file"
    else:
        source = "unknown"

    suffix = active[-6:] if len(active) >= 6 else active

    return {
        "env_file": str(ENV_FILE_PATH),
        "env_file_exists": ENV_FILE_PATH.is_file(),
        "token_source": source,
        "token_suffix": suffix,
        "os_env_overrides_file": bool(os_token and file_token and os_token != file_token),
    }
