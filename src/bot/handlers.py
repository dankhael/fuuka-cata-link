from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from src.bot.filters import AllowedChat, ContainsSupportedLink
from src.scrapers.base import MediaType, ScrapedMedia
from src.utils.formatters import format_caption, format_text_post, truncate
from src.utils.link_detector import DetectedLink
from src.utils.media_handler import download_media

logger = structlog.get_logger()

router = Router(name="media")

# Lazy-loaded scraper registry (populated in setup_scrapers)
_SCRAPER_MAP: dict = {}


def setup_scrapers() -> None:
    """Initialize scraper instances. Call once at startup."""
    from src.scrapers import SCRAPERS

    for scraper_cls in SCRAPERS:
        instance = scraper_cls()
        _SCRAPER_MAP[instance.platform] = instance
    logger.info("scrapers_loaded", platforms=list(_SCRAPER_MAP.keys()))


@router.message(AllowedChat(), ContainsSupportedLink())
async def handle_media_link(message: Message, detected_links: list[DetectedLink]) -> None:
    """Process a message that contains one or more supported social media links."""
    for link in detected_links:
        scraper = _SCRAPER_MAP.get(link.platform)
        if scraper is None:
            logger.warning("no_scraper_for_platform", platform=link.platform)
            continue

        try:
            result = await scraper.extract(link.url)
        except Exception as exc:
            logger.error(
                "scraper_error",
                platform=link.platform,
                url=link.url,
                error=str(exc),
            )
            await message.reply(f"Failed to extract media from {link.platform} link.")
            continue

        await _send_result(message, result)


async def _send_result(message: Message, result: ScrapedMedia) -> None:
    """Send the scraped result back to the chat."""
    if not result.has_media:
        # Text-only post
        text = format_text_post(result)
        await message.reply(truncate(text, max_len=4096))
        return

    # Download items that don't already have data (pre-downloaded by yt-dlp scrapers)
    items_needing_download = [item for item in result.media_items if item.data is None]
    items_already_downloaded = [item for item in result.media_items if item.data is not None]

    if items_needing_download:
        newly_downloaded = await download_media(items_needing_download)
    else:
        newly_downloaded = []

    downloaded = items_already_downloaded + newly_downloaded

    if not downloaded:
        await message.reply("Could not download media from this link.")
        return

    caption = truncate(format_caption(result))

    # Single media item — send directly
    if len(downloaded) == 1:
        item = downloaded[0]
        ext = "mp4" if item.media_type == MediaType.VIDEO else "jpg"
        file = BufferedInputFile(item.data, filename=f"media.{ext}")

        if item.media_type == MediaType.VIDEO:
            await message.reply_video(video=file, caption=caption)
        else:
            await message.reply_photo(photo=file, caption=caption)
        return

    # Multiple media items — send as a media group (album)
    media_group = []
    for i, item in enumerate(downloaded[:10]):  # Telegram allows max 10 in a group
        ext = "mp4" if item.media_type == MediaType.VIDEO else "jpg"
        file = BufferedInputFile(item.data, filename=f"media_{i}.{ext}")
        item_caption = caption if i == 0 else None

        if item.media_type == MediaType.VIDEO:
            media_group.append(InputMediaVideo(media=file, caption=item_caption))
        else:
            media_group.append(InputMediaPhoto(media=file, caption=item_caption))

    await message.reply_media_group(media=media_group)
