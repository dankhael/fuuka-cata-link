from __future__ import annotations

import re

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

_CURL_USER_AGENT = "curl/7.68.0"


class FacebookScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.FACEBOOK

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Download Facebook media via yt-dlp with cookie auth.

        For video posts, yt-dlp handles the download. For image-only posts
        (where yt-dlp reports "No video formats found"), falls back to
        scraping mbasic.facebook.com which serves simpler HTML.
        """
        # Try yt-dlp first (handles video posts)
        try:
            result = await ytdlp_download(url, cookies_file=settings.cookies_file)
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
        except RuntimeError as exc:
            logger.debug("facebook_ytdlp_failed", url=url, error=str(exc))

        # yt-dlp failed — try fdown for videos, then mbasic for images
        try:
            return await self._fdown_fallback(url)
        except Exception as exc:
            logger.debug("facebook_fdown_failed", url=url, error=str(exc))

        return await self._mbasic_fallback(url)

    async def _resolve_share_link(self, url: str) -> str:
        """Resolve Facebook /share/ shortlinks by following redirects.

        URLs like https://www.facebook.com/share/p/ABC123/ are shortlinks
        that redirect to the actual post URL.
        """
        if "/share/" not in url:
            return url

        # Use mbasic for resolution as it handles share links reliably with curl UA
        resolve_url = re.sub(
            r"https?://(?:www\.|m\.)?facebook\.com",
            "https://mbasic.facebook.com",
            url
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(  # distinct HEAD request often works better for redirects
                    resolve_url,
                    headers={"User-Agent": _CURL_USER_AGENT},
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resolved = str(resp.url)
                    logger.info("facebook_share_resolved", original=url, resolved=resolved)
                    return resolved
        except Exception as exc:
            logger.warning("facebook_share_resolve_failed", url=url, error=str(exc))
            return url

    async def _mbasic_fallback(self, url: str) -> ScrapedMedia:
        """Extract images from Facebook's basic mobile site.

        mbasic.facebook.com serves lightweight HTML that often includes
        direct image URLs even without authentication.
        """
        # Resolve /share/ shortlinks first — mbasic can't handle them
        resolved_url = await self._resolve_share_link(url)

        # Convert URL to mbasic.facebook.com
        mbasic_url = re.sub(
            r"https?://(?:www\.|m\.)?facebook\.com",
            "https://mbasic.facebook.com",
            resolved_url,
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(
                mbasic_url,
                headers={"User-Agent": _CURL_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

        # mbasic pages have images in <img> tags with full-size URLs
        # Look for images from Facebook's CDN
        # Better regex (catches lazy-loaded images + srcset)
        image_urls = re.findall(
            r'(?:src|data-src|srcset)=["\'](.*?(?:scontent|external|fbcdn).*?(?:jpg|jpeg|png|webp))["\']',
            html, re.IGNORECASE
        )

        # Also check for og:image in case mbasic serves it
        og_images = re.findall(
            r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        og_images += re.findall(
            r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?property=["\']og:image["\']',
            html,
            re.IGNORECASE,
        )

        # Combine and deduplicate, preferring larger images
        all_urls = og_images + image_urls  # og:image first (usually higher quality)
        seen = set()
        unique_urls = []
        for img_url in all_urls:
            # Unescape HTML entities
            img_url = img_url.replace("&amp;", "&")
            if img_url not in seen:
                seen.add(img_url)
                unique_urls.append(img_url)

        if not unique_urls:
            raise RuntimeError("Could not find any images on Facebook mbasic page")

        # Extract text content
        title_match = re.search(
            r'<meta\s+[^>]*?property=["\']og:title["\'][^>]*?content=["\']([^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        desc_match = re.search(
            r'<meta\s+[^>]*?property=["\']og:description["\'][^>]*?content=["\']([^"\']*)["\']',
            html,
            re.IGNORECASE,
        )

        # Download images
        media_items: list[MediaItem] = []
        for img_url in unique_urls[:5]:  # limit to 5 images
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        img_url,
                        headers={"User-Agent": _CURL_USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.read()
                        # Skip tiny images (likely icons/UI elements), min 5KB
                        if len(data) < 5 * 1024:
                            continue
                item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                item.data = data
                media_items.append(item)
            except Exception as exc:
                logger.warning("facebook_image_download_failed", url=img_url, error=str(exc))

        if not media_items:
            raise RuntimeError("Failed to download any images from Facebook post")

        caption = None
        if title_match:
            caption = title_match.group(1)
        elif desc_match:
            caption = desc_match.group(1)

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=None,
            caption=caption,
            media_items=media_items,
        )

    async def _fdown_fallback(self, url: str) -> ScrapedMedia:
        """Extract Facebook videos via fdown.net as a fallback."""
        fdown_url = "https://fdown.net/download.php"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                fdown_url,
                data={"URLz": url},
                headers={
                    "User-Agent": _CURL_USER_AGENT,
                    "Referer": "https://fdown.net/",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

        # fdown returns page with HD and SD download links
        hd_match = re.search(r'id="btn_download_hd"[^>]*href="([^"]+)"', html)
        sd_match = re.search(r'id="btn_download"[^>]*href="([^"]+)"', html)

        download_url = None
        if hd_match:
            download_url = hd_match.group(1)
        elif sd_match:
            download_url = sd_match.group(1)

        if not download_url:
            raise RuntimeError("fdown.net returned no download links")

        # Download the video
        async with aiohttp.ClientSession() as session:
            async with session.get(
                download_url,
                headers={"User-Agent": _CURL_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.read()

        if not data or len(data) < 10_000:
            raise RuntimeError("fdown.net returned empty or tiny file")

        item = MediaItem(url=download_url, media_type=MediaType.VIDEO)
        item.data = data

        # Try to extract title from fdown page
        title_match = re.search(r'<p[^>]*class="title"[^>]*>([^<]+)</p>', html)
        caption = title_match.group(1).strip() if title_match else None

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=None,
            caption=caption,
            media_items=[item],
        )
