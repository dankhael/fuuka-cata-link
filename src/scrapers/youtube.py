from __future__ import annotations

import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia, SkipExtraction
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult, is_proxy_connection_error, ytdlp_download

logger = structlog.get_logger()

_MAX_YOUTUBE_DURATION_SECONDS = 318


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable)."""
        return await self._ytdlp_extract(url)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        result = await self._download_with_proxy_fallback(url)

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

    async def _download_with_proxy_fallback(self, url: str) -> YtdlpResult:
        """Download via the configured residential proxy, falling back to a
        direct connection if the proxy itself is unreachable.

        The proxy (e.g. a home/Umbrel SOCKS5) exists to dodge YouTube's
        datacenter-IP bot-gate, but it must not become a single point of
        failure: if it's momentarily down we still try direct. A genuine
        extraction failure *through* the proxy is re-raised so the base
        fallback chain handles it normally.
        """
        cookies_file = settings.cookies_file
        proxy = settings.youtube_proxy
        if not proxy:
            return await ytdlp_download(url, cookies_file=cookies_file)

        try:
            return await ytdlp_download(url, cookies_file=cookies_file, proxy=proxy)
        except RuntimeError as exc:
            if not is_proxy_connection_error(str(exc)):
                raise
            logger.warning("youtube_proxy_unreachable", url=url, proxy=proxy, error=str(exc))
            return await ytdlp_download(url, cookies_file=cookies_file)
