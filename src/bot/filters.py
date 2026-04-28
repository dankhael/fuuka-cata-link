from __future__ import annotations

import re

from aiogram.filters import BaseFilter
from aiogram.types import Message

from src.config import settings
from src.utils.link_detector import DetectedLink, detect_links


class ContainsCommand(BaseFilter):
    """Filter that matches messages containing ``/command`` anywhere in the text.

    Unlike aiogram's built-in :class:`~aiogram.filters.Command`, which only
    fires when the command is the very first token, this filter accepts the
    command as a standalone token at any position — so users can write either
    ``/nocaption https://...`` or ``https://... /nocaption``.

    The optional ``@botname`` suffix Telegram appends in groups is tolerated.
    """

    def __init__(self, command: str) -> None:
        self.command = command
        # Standalone token: preceded by start-of-string or whitespace, followed
        # by end-of-string or whitespace. Optional @botname suffix is allowed.
        self.pattern = re.compile(
            rf"(?:^|\s)/{re.escape(command)}(?:@\w+)?(?=\s|$)"
        )

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return self.pattern.search(message.text) is not None


class ContainsSupportedLink(BaseFilter):
    """Filter that matches messages containing at least one supported social media link."""

    async def __call__(self, message: Message) -> bool | dict:
        if not message.text:
            return False
        links = detect_links(message.text)
        if not links:
            return False

        # Check if any links are wrapped in spoiler entities
        spoiler_spans = _get_spoiler_spans(message)
        if spoiler_spans:
            links = _mark_spoiler_links(links, message.text, spoiler_spans)

        return {"detected_links": links}


def _get_spoiler_spans(message: Message) -> list[tuple[int, int]]:
    """Extract (start, end) character spans of spoiler entities from the message."""
    if not message.entities:
        return []
    return [
        (entity.offset, entity.offset + entity.length)
        for entity in message.entities
        if entity.type == "spoiler"
    ]


def _mark_spoiler_links(
    links: list[DetectedLink],
    text: str,
    spoiler_spans: list[tuple[int, int]],
) -> list[DetectedLink]:
    """Return a new list of DetectedLinks with is_spoiler=True for links inside spoiler spans."""
    result = []
    for link in links:
        pos = text.find(link.url)
        if pos != -1 and any(start <= pos < end for start, end in spoiler_spans):
            result.append(DetectedLink(url=link.url, platform=link.platform, is_spoiler=True))
        else:
            result.append(link)
    return result


class AllowedChat(BaseFilter):
    """Filter that restricts the bot to whitelisted chats (if configured)."""

    async def __call__(self, message: Message) -> bool:
        if not settings.allowed_chats:
            return True  # no whitelist = allow all
        return message.chat.id in settings.allowed_chats
