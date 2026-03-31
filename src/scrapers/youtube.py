from __future__ import annotations

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable)."""
        return await self._ytdlp_extract(url)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        extra_args = ["--cookies", str(settings.cookies_file)] if settings.cookies_file else []

        result = await ytdlp_download(url, extra_args=extra_args)

        # Reject videos longer than 5 minutes 18 seconds
        if result.duration and result.duration > 318:
            minutes = int(result.duration // 60)
            seconds = int(result.duration % 60)
            return ScrapedMedia(
                platform=self.platform,
                original_url=url,
                caption=f"Video too long: {minutes}:{seconds:02d} (max 5:18)",
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
