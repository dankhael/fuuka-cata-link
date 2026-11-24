from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetUpdates
from aiogram.methods.base import Response, TelegramMethod, TelegramType

if TYPE_CHECKING:
    from aiogram import Bot

logger = structlog.get_logger()

_MAX_RETRIES = 3
# Telegram can ask for minutes or hours when a bot misbehaves. Past this, a late
# reply is worse than none and the handler would pin the media in memory.
_MAX_WAIT_SECONDS = 60


class FloodRetryMiddleware(BaseRequestMiddleware):
    """Wait out Telegram's flood control (429) and send the request again.

    A 429 means Telegram refused the request, so repeating it can't duplicate a
    message. Network timeouts are left alone: the message may have gone through.

    >>> bot.session.middleware(FloodRetryMiddleware())  # doctest: +SKIP
    """

    def __init__(
        self,
        max_retries: int = _MAX_RETRIES,
        max_wait_seconds: int = _MAX_WAIT_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._max_retries = max_retries
        self._max_wait_seconds = max_wait_seconds
        self._sleep = sleep

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        # Long polling already has its own backoff in aiogram's dispatcher.
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)

        for attempt in range(1, self._max_retries + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if exc.retry_after > self._max_wait_seconds:
                    raise
                _log_flood_wait(method, exc.retry_after, attempt)
                await self._sleep(exc.retry_after)
        return await make_request(bot, method)


def _log_flood_wait(method: TelegramMethod, retry_after: int, attempt: int) -> None:
    logger.warning(
        "telegram_flood_wait",
        method=type(method).__name__,
        chat_id=getattr(method, "chat_id", None),
        retry_after=retry_after,
        attempt=attempt,
    )
