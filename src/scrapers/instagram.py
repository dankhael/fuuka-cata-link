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
        extra_args = ["--cookies", str(settings.cookies_file)] if settings.cookies_file else []
        extra_args += ["--no-check-certificates"]

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

        return await self._smart_proxy_fallback(url)

    PROXY_SERVICES = [
        "https://imginn.com",
        "https://snapinsta.app",
        "https://kkinstagram.com",
        "https://igram.world",
    ]

    async def _smart_proxy_fallback(self, url: str) -> ScrapedMedia:
        for base_url in self.PROXY_SERVICES:
            try:
                proxy_url = re.sub(
                    r"https?://(?:www\.)?instagram\.com",
                    base_url, url,
                )

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        proxy_url,
                        headers={"User-Agent": _USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        html = await resp.text()

                # Extract media URLs (og:image + direct CDN links)
                media_urls = re.findall(
                    r'(https?://[^"\']+\.(?:jpg|jpeg|png|mp4|webp))', html, re.IGNORECASE
                )
                media_urls += re.findall(
                    r'content=["\']([^"\']+\.(?:jpg|jpeg|png|webp))', html
                )

                if not media_urls:
                    continue

                # Dedup + filter to real Instagram CDN media
                media_urls = list(dict.fromkeys(
                    u for u in media_urls if "scontent" in u or "cdn" in u
                ))

                media_items: list[MediaItem] = []
                for m_url in media_urls[:8]:
                    try:
                        async with aiohttp.ClientSession() as dl_session:
                            async with dl_session.get(
                                m_url,
                                headers={"User-Agent": _USER_AGENT},
                                timeout=aiohttp.ClientTimeout(total=15),
                            ) as dl_resp:
                                data = await dl_resp.read()
                    except Exception:
                        continue
                    if data and len(data) > 15_000:
                        mtype = MediaType.VIDEO if m_url.endswith(".mp4") else MediaType.IMAGE
                        item = MediaItem(url=m_url, media_type=mtype)
                        item.data = data
                        media_items.append(item)

                if media_items:
                    cap_match = re.search(
                        r'<meta\s+[^>]*?property=["\']og:description["\'][^>]*?content=["\']([^"\']*)["\']',
                        html, re.IGNORECASE,
                    )
                    caption = cap_match.group(1) if cap_match else None

                    return ScrapedMedia(
                        platform=self.platform,
                        original_url=url,
                        author=None,
                        caption=caption,
                        media_items=media_items,
                    )
            except Exception as e:
                logger.warning("instagram_proxy_failed", proxy=base_url, error=str(e))
                continue

        raise RuntimeError("All Instagram proxies failed")
