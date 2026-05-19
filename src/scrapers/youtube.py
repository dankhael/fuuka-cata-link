from __future__ import annotations

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia, SkipExtraction
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

_MAX_YOUTUBE_DURATION_SECONDS = 318


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable)."""
        return await self._ytdlp_extract(url)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        result = await ytdlp_download(url, cookies_file=settings.cookies_file)

        # Silently drop videos over the cap (DAN-71): replying with an error
        # message just spammed chats whenever someone shared a long video.
        if result.duration and result.duration > _MAX_YOUTUBE_DURATION_SECONDS:
            raise SkipExtraction(
                f"youtube duration {result.duration:.0f}s exceeds cap "
                f"{_MAX_YOUTUBE_DURATION_SECONDS}s for {url!r}"
            )

        if not result.data:
            raise RuntimeError("yt-dlp downloaded no data for YouTube URL")

        item = MediaItem(url=url, media_type=MediaType.VIDEO)
        item.data = result.data

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.title,
            media_items=[item],
        )
