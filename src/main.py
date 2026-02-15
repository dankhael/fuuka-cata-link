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
from src.config import settings


def configure_logging() -> None:
    """Set up structlog with JSON rendering for production, pretty for dev."""
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
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

    log.info("starting_bot", log_level=settings.log_level)

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

    log.info("bot_ready", bot_id=(await bot.get_me()).id)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
