from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia, SkipExtraction
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult, is_proxy_connection_error, ytdlp_download, ytdlp_info

logger = structlog.get_logger()

_MAX_YOUTUBE_DURATION_SECONDS = 318

T = TypeVar("T")


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable)."""
        return await self._ytdlp_extract(url)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        await self._skip_if_over_cap(url)

        result = await self._download_with_proxy_fallback(url)

        # Backstop cap check (DAN-71): the up-front probe is best-effort, so the
        # download's own metadata still enforces the cap when the probe couldn't.
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

    async def _skip_if_over_cap(self, url: str) -> None:
        """Probe duration up front and skip long videos before downloading.

        Checking after the download wastes a full (proxy-slow) transfer on
        videos we're going to drop, and silently no-ops when yt-dlp returns
        incomplete metadata (duration=None). Probe failures are swallowed — the
        download path stays the backstop — so a flaky metadata call never blocks
        a short, valid video (DAN-80).
        """
        try:
            info = await self._info_with_proxy_fallback(url)
        except RuntimeError:
            return
        duration = info.get("duration")
        if duration and duration > _MAX_YOUTUBE_DURATION_SECONDS:
            raise SkipExtraction(
                f"youtube duration {duration:.0f}s exceeds cap "
                f"{_MAX_YOUTUBE_DURATION_SECONDS}s for {url!r}"
            )

    async def _download_with_proxy_fallback(self, url: str) -> YtdlpResult:
        runner = functools.partial(ytdlp_download, cookies_file=settings.cookies_file)
        return await self._with_proxy_fallback(url, runner)

    async def _info_with_proxy_fallback(self, url: str) -> dict:
        return await self._with_proxy_fallback(url, ytdlp_info)

    async def _with_proxy_fallback(self, url: str, run: Callable[..., Awaitable[T]]) -> T:
        """Run *run*(url, proxy=...) through the residential proxy, retrying
        directly if the proxy itself is unreachable.

        The proxy (e.g. a home/Umbrel SOCKS5) exists to dodge YouTube's
        datacenter-IP bot-gate, but it must not become a single point of
        failure: if it's momentarily down we still try direct. A genuine
        extraction failure *through* the proxy is re-raised so the base
        fallback chain handles it normally.
        """
        proxy = settings.youtube_proxy
        if not proxy:
            return await run(url)

        try:
            return await run(url, proxy=proxy)
        except RuntimeError as exc:
            if not is_proxy_connection_error(str(exc)):
                raise
            logger.warning("youtube_proxy_unreachable", url=url, proxy=proxy, error=str(exc))
            return await run(url)
