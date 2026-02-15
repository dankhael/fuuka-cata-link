from __future__ import annotations

import asyncio
from io import BytesIO

import aiohttp
import structlog
from PIL import Image

from src.config import settings
from src.scrapers.base import MediaItem, MediaType

logger = structlog.get_logger()

_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


async def download_media(
    items: list[MediaItem],
    session: aiohttp.ClientSession | None = None,
) -> list[MediaItem]:
    """Download media items concurrently and populate their `data` field.

    Items exceeding MAX_FILE_SIZE_MB are skipped with a warning.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    sem = asyncio.Semaphore(settings.concurrent_downloads)

    async def _fetch(item: MediaItem) -> None:
        async with sem:
            try:
                async with session.get(
                    item.url,
                    timeout=aiohttp.ClientTimeout(total=settings.download_timeout_seconds),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    if len(data) > _MAX_BYTES:
                        logger.warning(
                            "media_too_large",
                            url=item.url,
                            size_mb=round(len(data) / 1024 / 1024, 1),
                        )
                        return
                    item.data = data
            except Exception as exc:
                logger.error("media_download_failed", url=item.url, error=str(exc))

    try:
        await asyncio.gather(*[_fetch(item) for item in items])
    finally:
        if own_session:
            await session.close()

    return [item for item in items if item.data is not None]


def optimize_image(data: bytes, max_dimension: int = 1920, quality: int = 85) -> bytes:
    """Compress an image while preserving reasonable quality."""
    img = Image.open(BytesIO(data))
    img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
    buf = BytesIO()
    fmt = "JPEG" if img.mode == "RGB" else "PNG"
    img.save(buf, format=fmt, quality=quality, optimize=True)
    return buf.getvalue()


def is_image(item: MediaItem) -> bool:
    return item.media_type == MediaType.IMAGE


def is_video(item: MediaItem) -> bool:
    return item.media_type == MediaType.VIDEO
