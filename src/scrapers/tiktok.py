from __future__ import annotations

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

_TIKWM_API = "https://www.tikwm.com/api/"
_TIKWM_TIMEOUT = aiohttp.ClientTimeout(total=15)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=settings.download_timeout_seconds)
_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


class TikTokScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TIKTOK

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use tikwm.com API for extraction (handles both photos and videos)."""
        async with aiohttp.ClientSession() as session:
            data = await self._fetch_tikwm(session, url)

            is_photo_post = bool(data.get("images"))

            if is_photo_post:
                media_items = await self._extract_photos(session, data)
            else:
                media_items = await self._extract_video(session, data)

            author = data.get("author", {}).get("unique_id")
            caption = data.get("title")

            return ScrapedMedia(
                platform=self.platform,
                original_url=url,
                author=author,
                caption=caption,
                media_items=media_items,
            )

    @staticmethod
    async def _fetch_tikwm(session: aiohttp.ClientSession, url: str) -> dict:
        """Call tikwm.com API and return the post data dict."""
        async with session.get(
            _TIKWM_API,
            params={"url": url},
            timeout=_TIKWM_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

        if payload.get("code") != 0:
            msg = payload.get("msg", "unknown error")
            raise RuntimeError(f"tikwm API error: {msg}")

        data = payload.get("data")
        if not data:
            raise RuntimeError("tikwm API returned empty data")

        return data

    async def _extract_photos(
        self, session: aiohttp.ClientSession, data: dict
    ) -> list[MediaItem]:
        """Download photo carousel images and return MediaItems with pre-populated data."""
        image_urls: list[str] = data.get("images", [])
        if not image_urls:
            raise RuntimeError("tikwm reported photo post but images list is empty")

        media_items: list[MediaItem] = []
        for img_url in image_urls[:10]:  # Telegram media group limit
            try:
                async with session.get(img_url, timeout=_DOWNLOAD_TIMEOUT) as resp:
                    resp.raise_for_status()
                    img_data = await resp.read()

                if len(img_data) > _MAX_BYTES:
                    logger.warning(
                        "tiktok_image_too_large",
                        url=img_url,
                        size_mb=round(len(img_data) / 1024 / 1024, 1),
                    )
                    continue

                item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                item.data = img_data
                media_items.append(item)
            except Exception as exc:
                logger.warning("tiktok_image_download_failed", url=img_url, error=str(exc))

        if not media_items:
            raise RuntimeError("Failed to download any images from TikTok photo post")

        return media_items

    async def _extract_video(
        self, session: aiohttp.ClientSession, data: dict
    ) -> list[MediaItem]:
        """Download video from tikwm data and return MediaItems with pre-populated data."""
        video_url = data.get("hdplay") or data.get("play")
        if not video_url:
            raise RuntimeError("tikwm returned no video URL")

        async with session.get(video_url, timeout=_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            video_data = await resp.read()

        if not video_data:
            raise RuntimeError("tikwm returned empty video data")

        if len(video_data) > _MAX_BYTES:
            raise RuntimeError(
                f"TikTok video too large: {round(len(video_data) / 1024 / 1024, 1)}MB"
            )

        item = MediaItem(url=video_url, media_type=MediaType.VIDEO)
        item.data = video_data
        return [item]

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        """Fallback to yt-dlp for video posts.

        TikTok CDN URLs are signed and reject direct HTTP downloads,
        so yt-dlp handles the full download pipeline.
        Note: yt-dlp does NOT support TikTok photo posts.
        """
        result = await ytdlp_download(url)

        if not result.data:
            raise RuntimeError("yt-dlp downloaded no data for TikTok URL")

        item = MediaItem(url=url, media_type=MediaType.VIDEO)
        item.data = result.data

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.description or result.title,
            media_items=[item],
        )
