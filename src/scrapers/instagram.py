from __future__ import annotations

import re

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class InstagramScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.INSTAGRAM

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Extract Instagram media: yt-dlp (videos) → gallery-dl (images) → embed page."""
        extra_args = ["--cookies", str(settings.cookies_file)] if settings.cookies_file else []
        extra_args += ["--no-check-certificates"]

        # Phase 1: yt-dlp — handles reels/videos reliably
        try:
            result = await ytdlp_download(url, extra_args=extra_args)
            if result.data:
                media_type = MediaType.VIDEO if result.is_video else MediaType.IMAGE
                item = MediaItem(url=url, media_type=media_type)
                item.data = result.data
                return ScrapedMedia(
                    platform=self.platform,
                    original_url=url,
                    author=result.uploader,
                    caption=result.description or result.title,
                    media_items=[item],
                )
        except Exception as e:
            logger.debug("instagram_ytdlp_failed", url=url, error=str(e))

        # Phase 2: gallery-dl — handles image posts and carousels
        try:
            return await self._gallery_dl_extract(url)
        except Exception as e:
            logger.debug("instagram_gallery_dl_failed", url=url, error=str(e))

        # Phase 3: embed page fallback — lightweight, no extra deps
        return await self._embed_fallback(url)

    async def _gallery_dl_extract(self, url: str) -> ScrapedMedia:
        """Extract Instagram media via gallery-dl."""
        from src.utils.gallery_dl import gallery_dl_download

        result = await gallery_dl_download(url, cookies_file=settings.cookies_file)

        media_items: list[MediaItem] = []
        for f in result.files:
            media_type = MediaType.VIDEO if f.is_video else MediaType.IMAGE
            item = MediaItem(url=url, media_type=media_type)
            item.data = f.data
            media_items.append(item)

        if not media_items:
            raise RuntimeError("gallery-dl returned no usable media for Instagram")

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.description or result.title,
            media_items=media_items,
        )

    async def _embed_fallback(self, url: str) -> ScrapedMedia:
        """Extract images from Instagram's embed page.

        The /embed/captioned/ endpoint returns simpler HTML that often includes
        og:image and CDN URLs even without authentication.
        """
        shortcode_match = re.search(r"/(?:p|reel|reels)/([A-Za-z0-9_-]+)", url)
        if not shortcode_match:
            raise RuntimeError("Could not extract Instagram shortcode from URL")

        shortcode = shortcode_match.group(1)
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                embed_url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

        # Extract image URLs from embed page
        image_urls: list[str] = []

        # og:image meta tag (most reliable single-image source)
        og_matches = re.findall(
            r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        image_urls.extend(og_matches)

        # Instagram CDN image URLs in the page body
        cdn_urls = re.findall(
            r'(https?://(?:scontent|instagram)[^"\'\\>\s]+\.(?:jpg|jpeg|png|webp))',
            html,
            re.IGNORECASE,
        )
        image_urls.extend(cdn_urls)

        # Deduplicate
        seen: set[str] = set()
        unique_urls: list[str] = []
        for img_url in image_urls:
            img_url = img_url.replace("&amp;", "&")
            if img_url not in seen:
                seen.add(img_url)
                unique_urls.append(img_url)

        if not unique_urls:
            raise RuntimeError("Instagram embed page returned no image URLs")

        # Download images
        media_items: list[MediaItem] = []
        async with aiohttp.ClientSession() as dl_session:
            for img_url in unique_urls[:10]:
                try:
                    async with dl_session.get(
                        img_url,
                        headers={"User-Agent": _USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        data = await resp.read()
                    if data and len(data) > 5_000:
                        item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                        item.data = data
                        media_items.append(item)
                except Exception:
                    continue

        if not media_items:
            raise RuntimeError("Could not download any images from Instagram embed")

        cap_match = re.search(
            r'<meta\s+[^>]*?property=["\']og:description["\'][^>]*?content=["\']([^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        caption = cap_match.group(1) if cap_match else None

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=None,
            caption=caption,
            media_items=media_items,
        )
