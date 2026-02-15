from __future__ import annotations

import aiohttp

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download


class TwitterScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TWITTER

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use the vxtwitter/fxtwitter API for easy extraction."""
        # Convert twitter.com/x.com URL to api.vxtwitter.com
        api_url = url.replace("twitter.com", "api.vxtwitter.com").replace(
            "x.com", "api.vxtwitter.com"
        )

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json()

        media_items: list[MediaItem] = []
        for media in data.get("media_extended", []):
            media_type = media.get("type", "")
            if media_type == "image":
                media_items.append(MediaItem(url=media["url"], media_type=MediaType.IMAGE))
            elif media_type == "video":
                media_items.append(MediaItem(url=media["url"], media_type=MediaType.VIDEO))

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=data.get("user_name"),
            caption=data.get("text"),
            media_items=media_items,
        )

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        """Fallback to yt-dlp for video tweets."""
        extra_args = []
        if settings.twitter_bearer_token:
            extra_args = ["--extractor-args", f"twitter:bearer_token={settings.twitter_bearer_token}"]

        result = await ytdlp_download(url, extra_args=extra_args)
        if not result.data:
            raise RuntimeError("yt-dlp downloaded no data for Twitter URL")

        item = MediaItem(url=url, media_type=MediaType.VIDEO)
        item.data = result.data

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.description,
            media_items=[item],
        )
