from __future__ import annotations

from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download


class TikTokScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TIKTOK

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Download TikTok video via yt-dlp directly.

        TikTok CDN URLs are signed and reject direct HTTP downloads,
        so we must let yt-dlp handle the full download pipeline.
        """
        result = await ytdlp_download(url)

        media_items: list[MediaItem] = []
        if result.data:
            item = MediaItem(url=url, media_type=MediaType.VIDEO)
            item.data = result.data
            media_items.append(item)

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.description or result.title,
            media_items=media_items,
        )
