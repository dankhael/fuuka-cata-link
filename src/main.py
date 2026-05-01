from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.bot.handlers import router, setup_scrapers
from src.bot.middlewares import LoggingMiddleware, RateLimitMiddleware
from src.config import env_diagnostics, settings
from src.utils.diagnostics import error_diagnostics_processor, performance_processor


def configure_logging() -> None:
    """Set up structlog with JSON rendering for production, pretty for dev."""
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            error_diagnostics_processor,
            performance_processor,
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


async def main() -> None:
    configure_logging()
    log = structlog.get_logger()

    diag = env_diagnostics()
    log.info("starting_bot", log_level=settings.log_level, **diag)
    if diag["os_env_overrides_file"]:
        log.warning(
            "os_env_overriding_env_file",
            env_file=diag["env_file"],
            hint="TELEGRAM_BOT_TOKEN is set in the OS environment and shadows the .env file. "
            "Unset it (e.g. `unset TELEGRAM_BOT_TOKEN` / `Remove-Item Env:TELEGRAM_BOT_TOKEN`) "
            "or run with `env -i ENV_FILE=... python -m src.main`.",
        )

    setup_scrapers()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register middlewares
    dp.message.middleware(LoggingMiddleware())
    dp.message.middleware(RateLimitMiddleware())

    # Register routers
    dp.include_router(router)

    me = await bot.get_me()
    log.info("bot_ready", bot_id=me.id, bot_username=me.username)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
