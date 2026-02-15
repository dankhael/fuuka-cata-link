from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import Message

from src.config import settings
from src.utils.link_detector import detect_links


class ContainsSupportedLink(BaseFilter):
    """Filter that matches messages containing at least one supported social media link."""

    async def __call__(self, message: Message) -> bool | dict:
        if not message.text:
            return False
        links = detect_links(message.text)
        if not links:
            return False
        return {"detected_links": links}


class AllowedChat(BaseFilter):
    """Filter that restricts the bot to whitelisted chats (if configured)."""

    async def __call__(self, message: Message) -> bool:
        if not settings.allowed_chats:
            return True  # no whitelist = allow all
        return message.chat.id in settings.allowed_chats
