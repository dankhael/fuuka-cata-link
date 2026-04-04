from __future__ import annotations

import time

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    ReplyParameters,
)

from src.bot.filters import AllowedChat, ContainsSupportedLink
from src.config import settings
from src.scrapers.base import MediaType, ScrapedMedia
from src.utils.formatters import format_caption, format_text_post, truncate
from src.utils.link_detector import DetectedLink
from src.utils.media_handler import download_media, ensure_within_limit

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


async def _process_links(
    message: Message,
    detected_links: list[DetectedLink],
    *,
    strip_referenced: bool = False,
) -> None:
    """Scrape each detected link and send results.

    When *strip_referenced* is True, any referenced_post (quote/reply parent)
    is stripped before sending — the bot sends only the linked post itself.
    """
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

        if strip_referenced:
            result.referenced_post = None
            result.reference_type = None

        await _send_result(message, result, has_spoiler=link.is_spoiler)


HELP_TEXT = """<b>Fuuka</b> — expansora de links de redes sociais

Eu extraio e reencaminho automaticamente mídias de links compartilhados neste chat.

<b>Plataformas suportadas:</b>
• Twitter / X
• YouTube
• Instagram (somente reels no momento)
• TikTok
• Facebook (somente reels no momento)
• GitHub (repositórios, PRs, issues)
• Reddit

<b>Comandos:</b>
/help — exibir esta mensagem
/ignore — postar um link sem eu expandir
/noreply — expandir o link mas ocultar o post citado/respondido

<b>Como funciona:</b>
Basta compartilhar um link suportado e eu respondo com a mídia ou conteúdo do post. Para spoilers, coloque o link em uma tag de spoiler antes."""


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Send a help message explaining Fuuka's features and commands."""
    await message.reply(HELP_TEXT, parse_mode="HTML")


@router.message(Command("ignore"), AllowedChat(), ContainsSupportedLink())
async def handle_ignore(message: Message, detected_links: list[DetectedLink]) -> None:
    """Silently ignore a supported link — do not scrape or reply."""
    logger.debug("link_ignored", urls=[link.url for link in detected_links])


@router.message(Command("noreply"), AllowedChat(), ContainsSupportedLink())
async def handle_noreply(message: Message, detected_links: list[DetectedLink]) -> None:
    """Scrape the link but strip the referenced post (quote/reply parent)."""
    await _process_links(message, detected_links, strip_referenced=True)


@router.message(AllowedChat(), ContainsSupportedLink())
async def handle_media_link(message: Message, detected_links: list[DetectedLink]) -> None:
    """Process a message that contains one or more supported social media links."""
    await _process_links(message, detected_links)


async def _send_result(
    message: Message, result: ScrapedMedia, *, has_spoiler: bool = False
) -> None:
    """Send the scraped result back to the chat.

    If the result has a referenced_post (reply or quote), sends the referenced
    post first, then sends the main result as a Telegram reply to it.
    """
    reply_to_message_id: int | None = None

    if result.referenced_post:
        ref_msg = await _send_single_result(
            message, result.referenced_post, has_spoiler=has_spoiler
        )
        if ref_msg is not None:
            reply_to_message_id = ref_msg.message_id

    await _send_single_result(
        message, result,
        reply_to_message_id=reply_to_message_id,
        has_spoiler=has_spoiler,
    )


async def _send_single_result(
    message: Message,
    result: ScrapedMedia,
    reply_to_message_id: int | None = None,
    has_spoiler: bool = False,
) -> Message | None:
    """Send a single ScrapedMedia and return the sent Message.

    When *reply_to_message_id* is ``None`` the message replies to the user's
    original message (default ``message.reply_*`` behaviour).  When set, it
    uses ``message.answer_*`` with an explicit ``ReplyParameters`` so the bot
    message replies to a different message in the chat.
    """
    reply_params = (
        ReplyParameters(message_id=reply_to_message_id) if reply_to_message_id else None
    )

    if not result.has_media:
        text = format_text_post(result)
        if reply_params:
            return await message.answer(truncate(text, max_len=4096), reply_parameters=reply_params)
        return await message.reply(truncate(text, max_len=4096))

    # Download items that don't already have data
    items_needing_download = [item for item in result.media_items if item.data is None]
    items_already_downloaded = [item for item in result.media_items if item.data is not None]

    if items_needing_download:
        newly_downloaded = await download_media(items_needing_download)
    else:
        newly_downloaded = []

    downloaded = items_already_downloaded + newly_downloaded
    downloaded = await ensure_within_limit(
        downloaded, settings.auto_download_limit_mb * 1024 * 1024
    )

    if not downloaded:
        await message.reply("Could not download media from this link.")
        return None

    caption = truncate(format_caption(result))

    send_start = time.monotonic()

    # Single media item
    if len(downloaded) == 1:
        item = downloaded[0]
        if item.media_type == MediaType.VIDEO:
            ext = "mp4"
        elif item.media_type == MediaType.ANIMATION:
            ext = "gif"
        else:
            ext = "jpg"
        file = BufferedInputFile(item.data, filename=f"media.{ext}")

        if item.media_type == MediaType.VIDEO:
            if reply_params:
                sent = await message.answer_video(
                    video=file, caption=caption, has_spoiler=has_spoiler,
                    reply_parameters=reply_params,
                )
            else:
                sent = await message.reply_video(
                    video=file, caption=caption, has_spoiler=has_spoiler,
                )
        elif item.media_type == MediaType.ANIMATION:
            if reply_params:
                sent = await message.answer_animation(
                    animation=file, caption=caption, has_spoiler=has_spoiler,
                    reply_parameters=reply_params,
                )
            else:
                sent = await message.reply_animation(
                    animation=file, caption=caption, has_spoiler=has_spoiler,
                )
        else:
            if reply_params:
                sent = await message.answer_photo(
                    photo=file, caption=caption, has_spoiler=has_spoiler,
                    reply_parameters=reply_params,
                )
            else:
                sent = await message.reply_photo(
                    photo=file, caption=caption, has_spoiler=has_spoiler,
                )
        logger.info(
            "media_sent",
            platform=result.platform,
            media_count=1,
            duration_ms=int((time.monotonic() - send_start) * 1000),
        )
        return sent

    # Multiple media items — send as a media group (album)
    media_group = []
    for i, item in enumerate(downloaded[:10]):  # Telegram allows max 10 in a group
        if item.media_type == MediaType.VIDEO:
            ext = "mp4"
        elif item.media_type == MediaType.ANIMATION:
            ext = "gif"
        else:
            ext = "jpg"
        file = BufferedInputFile(item.data, filename=f"media_{i}.{ext}")
        item_caption = caption if i == 0 else None

        if item.media_type == MediaType.VIDEO:
            media_group.append(
                InputMediaVideo(media=file, caption=item_caption, has_spoiler=has_spoiler)
            )
        elif item.media_type == MediaType.ANIMATION:
            media_group.append(
                InputMediaAnimation(media=file, caption=item_caption, has_spoiler=has_spoiler)
            )
        else:
            media_group.append(
                InputMediaPhoto(media=file, caption=item_caption, has_spoiler=has_spoiler)
            )

    if reply_params:
        sent = await message.answer_media_group(
            media=media_group, reply_parameters=reply_params
        )
    else:
        sent = await message.reply_media_group(media=media_group)
    logger.info(
        "media_sent",
        platform=result.platform,
        media_count=len(media_group),
        duration_ms=int((time.monotonic() - send_start) * 1000),
    )
    return sent[0] if sent else None
