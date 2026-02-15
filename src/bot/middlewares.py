from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = structlog.get_logger()


class LoggingMiddleware(BaseMiddleware):
    """Log every incoming message with structured context."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        start = time.monotonic()
        logger.info(
            "message_received",
            user_id=event.from_user.id if event.from_user else None,
            chat_id=event.chat.id,
            text_preview=event.text[:80] if event.text else None,
        )
        try:
            return await handler(event, data)
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.debug("message_handled", duration_ms=duration_ms)


class RateLimitMiddleware(BaseMiddleware):
    """Simple per-user rate limiter.

    Allows `max_requests` per `window_seconds`. Excess messages are silently dropped.
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[int, list[float]] = defaultdict(list)
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else 0
        now = time.monotonic()

        # Prune old entries
        self._requests[user_id] = [
            t for t in self._requests[user_id] if now - t < self._window
        ]

        if len(self._requests[user_id]) >= self._max:
            logger.warning("rate_limited", user_id=user_id, chat_id=event.chat.id)
            return None

        self._requests[user_id].append(now)
        return await handler(event, data)
