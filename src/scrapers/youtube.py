from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia, SkipExtraction
from src.utils.link_detector import Platform
from src.utils.proxy import is_proxy_reachable, redact_proxy_credentials
from src.utils.ytdlp import YtdlpResult, expected_filesize, ytdlp_download, ytdlp_info

logger = structlog.get_logger()

_MAX_YOUTUBE_DURATION_SECONDS = 318
_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024

T = TypeVar("T")


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable).

        Deliberately *not* also exposed as ``_ytdlp_extract``: the base chain
        calls both, so aliasing them ran the whole probe+download twice per link.
        """
        await self._require_sendable(url)

        result = await self._download_with_proxy_fallback(url)

        # Backstop cap check (DAN-71): metadata can still differ between the
        # probe and the download (format reselection, a lying extractor), so
        # the download's own numbers get the final say.
        if result.duration and result.duration > _MAX_YOUTUBE_DURATION_SECONDS:
            raise SkipExtraction(
                f"youtube duration {result.duration:.0f}s exceeds cap "
                f"{_MAX_YOUTUBE_DURATION_SECONDS}s for {url!r}"
            )

        # Oversized is "unsupported", not "broken": an over-limit video used to
        # surface as data=None and reach the chat as an extraction error.
        if result.exceeds_size_limit:
            raise SkipExtraction(
                f"youtube video exceeds the {settings.max_file_size_mb}MB send cap for {url!r}"
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

    async def _require_sendable(self, url: str) -> None:
        """Prove from metadata that the video is worth downloading, or skip it.

        Downloading first wastes a full (proxy-slow) transfer on videos we are
        going to drop anyway (DAN-80), so the caps are checked up front.

        A probe that fails or comes back without a duration is skipped too,
        rather than downloaded on spec as it was before: the probe and the
        download share one yt-dlp auth path, so a bot-gated or unreachable
        probe predicts a bot-gated download. Gating on a *successful* check
        means everything that reaches the download was known-sendable, and a
        failure there is worth reporting to the chat — the chat never sees an
        error for a video we were never going to post.
        """
        try:
            info = await self._info_with_proxy_fallback(url)
        except RuntimeError as exc:
            logger.warning("youtube_probe_failed", url=url, error=str(exc))
            raise SkipExtraction(f"youtube metadata probe failed for {url!r}: {exc}") from exc

        duration = info.get("duration")
        if not duration:
            logger.warning("youtube_probe_without_duration", url=url)
            raise SkipExtraction(f"youtube metadata carries no duration for {url!r}")
        if duration > _MAX_YOUTUBE_DURATION_SECONDS:
            raise SkipExtraction(
                f"youtube duration {duration:.0f}s exceeds cap "
                f"{_MAX_YOUTUBE_DURATION_SECONDS}s for {url!r}"
            )

        # Size may legitimately be unknown; the download enforces the cap itself
        # (--max-filesize + the post-download check), and skips just as quietly.
        size = expected_filesize(info)
        if size and size > _MAX_BYTES:
            raise SkipExtraction(
                f"youtube filesize {size / 1024 / 1024:.0f}MB exceeds cap "
                f"{settings.max_file_size_mb}MB for {url!r}"
            )

    async def _download_with_proxy_fallback(self, url: str) -> YtdlpResult:
        runner = functools.partial(ytdlp_download, cookies_file=settings.cookies_file)
        return await self._with_proxy_fallback(url, runner)

    async def _info_with_proxy_fallback(self, url: str) -> dict:
        return await self._with_proxy_fallback(url, ytdlp_info)

    async def _with_proxy_fallback(self, url: str, run: Callable[..., Awaitable[T]]) -> T:
        """Run *run*(url, proxy=...) through the residential proxy, falling back
        to a direct call whenever the proxied attempt doesn't pan out.

        The proxy (e.g. a home/Umbrel SOCKS5) exists to dodge YouTube's
        datacenter-IP bot-gate, but it must not become a single point of
        failure. Two guards keep that promise:

        1. A TCP probe *before* the call, so a dead proxy costs one refused
           connect instead of yt-dlp's retries and wall-clock ceiling. The probe
           is the only reliable signal — yt-dlp reports a refused proxy as an
           OS-localized "connection refused" that never mentions the proxy.
        2. One direct retry if the proxied call fails anyway (SOCKS auth reject,
           or the proxy dying mid-transfer). This replaces the base chain's
           second yt-dlp attempt rather than adding to it.
        """
        proxy = settings.youtube_proxy
        if not proxy:
            return await run(url)

        safe_proxy = redact_proxy_credentials(proxy)
        if not await is_proxy_reachable(proxy):
            logger.warning("youtube_proxy_unreachable", url=url, proxy=safe_proxy)
            return await run(url)

        try:
            return await run(url, proxy=proxy)
        except RuntimeError as exc:
            logger.warning(
                "youtube_proxy_attempt_failed", url=url, proxy=safe_proxy, error=str(exc)
            )
            return await run(url)
