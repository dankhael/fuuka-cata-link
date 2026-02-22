from __future__ import annotations

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

_FX_API_BASE = "https://api.fxtwitter.com"
_FX_TIMEOUT = aiohttp.ClientTimeout(total=15)


class TwitterScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TWITTER

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use the fxtwitter API for extraction with reply/quote support."""
        api_url = self._to_api_url(url)

        async with aiohttp.ClientSession() as session:
            tweet_data = await self._fetch_tweet(session, api_url)
            result = self._parse_tweet(tweet_data, url)

            # Handle quote tweets (inline data, no second call needed)
            quote_data = tweet_data.get("quote")
            if quote_data:
                quoted_url = quote_data.get("url", url)
                result.referenced_post = self._parse_tweet(quote_data, quoted_url)
                result.reference_type = "quote"

            # Handle replies (requires second API call for parent tweet)
            # replying_to is a plain string (screen_name), replying_to_status is the tweet ID
            elif tweet_data.get("replying_to_status"):
                parent_screen_name = tweet_data.get("replying_to", "_")
                parent_id = tweet_data["replying_to_status"]
                if parent_id:
                    parent_api_url = (
                        f"{_FX_API_BASE}/{parent_screen_name}/status/{parent_id}"
                    )
                    try:
                        parent_data = await self._fetch_tweet(session, parent_api_url)
                        parent_url = parent_data.get(
                            "url",
                            f"https://x.com/{parent_screen_name}/status/{parent_id}",
                        )
                        result.referenced_post = self._parse_tweet(parent_data, parent_url)
                        result.reference_type = "reply"
                    except Exception:
                        logger.warning(
                            "parent_tweet_fetch_failed",
                            parent_id=parent_id,
                            url=url,
                        )

            return result

    @staticmethod
    def _to_api_url(url: str) -> str:
        """Convert a twitter.com / x.com URL to api.fxtwitter.com."""
        return url.replace("twitter.com", "api.fxtwitter.com").replace(
            "x.com", "api.fxtwitter.com"
        )

    @staticmethod
    async def _fetch_tweet(session: aiohttp.ClientSession, api_url: str) -> dict:
        """Fetch and unwrap a single tweet from the fxtwitter API."""
        async with session.get(api_url, timeout=_FX_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("tweet", data)

    def _parse_tweet(self, tweet_data: dict, original_url: str) -> ScrapedMedia:
        """Parse an fxtwitter API tweet dict into a ScrapedMedia."""
        media_items: list[MediaItem] = []

        media_obj = tweet_data.get("media")
        if media_obj:
            all_media = media_obj.get("all")
            if all_media:
                for item in all_media:
                    media_items.append(self._parse_media_item(item))
            else:
                for photo in media_obj.get("photos", []):
                    media_items.append(MediaItem(url=photo["url"], media_type=MediaType.IMAGE))
                for video in media_obj.get("videos", []):
                    media_items.append(MediaItem(url=video["url"], media_type=MediaType.VIDEO))

        author = None
        author_obj = tweet_data.get("author")
        if author_obj:
            author = author_obj.get("screen_name") or author_obj.get("name")

        return ScrapedMedia(
            platform=self.platform,
            original_url=original_url,
            author=author,
            caption=tweet_data.get("text"),
            media_items=media_items,
        )

    @staticmethod
    def _parse_media_item(item: dict) -> MediaItem:
        """Parse a single media entry from fxtwitter's media.all[]."""
        item_type = item.get("type", "")
        if item_type in ("video", "gif"):
            return MediaItem(url=item["url"], media_type=MediaType.VIDEO)
        return MediaItem(url=item["url"], media_type=MediaType.IMAGE)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        """Fallback to yt-dlp for video tweets."""
        extra_args = []
        if settings.twitter_bearer_token:
            extra_args = [
                "--extractor-args",
                f"twitter:bearer_token={settings.twitter_bearer_token}",
            ]

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
