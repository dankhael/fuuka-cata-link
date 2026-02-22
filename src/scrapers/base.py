from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from src.utils.link_detector import Platform

logger = structlog.get_logger()


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    TEXT = "text"
    CODE = "code"


@dataclass
class MediaItem:
    """A single media attachment (image or video)."""

    url: str
    media_type: MediaType
    data: bytes | None = None  # downloaded content, filled by media_handler


@dataclass
class ScrapedMedia:
    """Result of scraping a social media link."""

    platform: Platform
    original_url: str
    author: str | None = None
    caption: str | None = None
    media_items: list[MediaItem] = field(default_factory=list)
    method_used: str = "unknown"
    referenced_post: ScrapedMedia | None = None
    reference_type: str | None = None  # "reply" | "quote"

    @property
    def has_media(self) -> bool:
        return len(self.media_items) > 0


class BaseScraper(ABC):
    """Abstract base class for all platform scrapers.

    Subclasses must implement `platform` and at least `_primary_extract`.
    The fallback chain runs: _primary_extract -> _ytdlp_extract -> _browser_extract.
    """

    @property
    @abstractmethod
    def platform(self) -> Platform: ...

    async def extract(self, url: str) -> ScrapedMedia:
        """Run the extraction fallback chain for the given URL."""
        methods: list[tuple[str, callable]] = [
            ("primary", self._primary_extract),
            ("yt-dlp", self._ytdlp_extract),
            ("browser", self._browser_extract),
        ]

        for method_name, method in methods:
            start = time.monotonic()
            try:
                result = await method(url)
                duration_ms = int((time.monotonic() - start) * 1000)
                result.method_used = method_name
                logger.info(
                    "media_extracted",
                    platform=self.platform,
                    url=url,
                    method=method_name,
                    duration_ms=duration_ms,
                    media_count=len(result.media_items),
                )
                return result
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.warning(
                    "extraction_method_failed",
                    platform=self.platform,
                    url=url,
                    method=method_name,
                    duration_ms=duration_ms,
                    error=str(exc),
                )

        logger.error("all_extraction_methods_failed", platform=self.platform, url=url)
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            caption="Could not extract media from this link.",
            method_used="none",
        )

    @abstractmethod
    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Platform-specific primary extraction method."""
        ...

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        """Fallback extraction using yt-dlp. Override if the platform supports it."""
        raise NotImplementedError("yt-dlp extraction not implemented for this platform")

    async def _browser_extract(self, url: str) -> ScrapedMedia:
        """Last-resort extraction using a headless browser."""
        raise NotImplementedError("Browser extraction not implemented for this platform")
