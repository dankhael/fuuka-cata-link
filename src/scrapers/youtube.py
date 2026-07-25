from __future__ import annotations

import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia, SkipExtraction
from src.utils.link_detector import Platform
from src.utils.proxy import is_proxy_reachable, redact_proxy_credentials
from src.utils.ytdlp import YtdlpResult, ytdlp_download

logger = structlog.get_logger()

_MAX_YOUTUBE_DURATION_SECONDS = 318


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable).

        Deliberately *not* also exposed as ``_ytdlp_extract``: the base chain
        calls both, so aliasing them ran the same download twice per link.
        """
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
        direct connection whenever the proxied attempt doesn't pan out.

        The proxy (e.g. a home/Umbrel SOCKS5) exists to dodge YouTube's
        datacenter-IP bot-gate, but it must not become a single point of
        failure. Two guards keep that promise:

        1. A TCP probe *before* the download, so a dead proxy costs one refused
           connect instead of yt-dlp's three-retry storm. The probe is the only
           reliable signal — yt-dlp reports a refused proxy as an OS-localized
           "connection refused" that never mentions the proxy at all.
        2. One direct retry if the proxied download fails anyway (SOCKS auth
           reject, or the proxy dying mid-transfer). This replaces the base
           chain's second yt-dlp attempt rather than adding to it.
        """
        cookies_file = settings.cookies_file
        proxy = settings.youtube_proxy
        if not proxy:
            return await ytdlp_download(url, cookies_file=cookies_file)

        safe_proxy = redact_proxy_credentials(proxy)
        if not await is_proxy_reachable(proxy):
            logger.warning("youtube_proxy_unreachable", url=url, proxy=safe_proxy)
            return await ytdlp_download(url, cookies_file=cookies_file)

        try:
            return await ytdlp_download(url, cookies_file=cookies_file, proxy=proxy)
        except RuntimeError as exc:
            logger.warning(
                "youtube_proxy_attempt_failed", url=url, proxy=safe_proxy, error=str(exc)
            )
            return await ytdlp_download(url, cookies_file=cookies_file)
