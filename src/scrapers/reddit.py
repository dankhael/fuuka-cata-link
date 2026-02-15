from __future__ import annotations

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

# Rotate User-Agents to reduce blocking
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class RedditScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.REDDIT

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Fetch post data via Reddit's public JSON API."""
        # Resolve /s/ share shortlinks by following the redirect
        resolved_url = await self._resolve_shortlink(url)

        # Build the JSON URL — strip trailing slash, handle query params
        clean_url = resolved_url.split("?")[0].rstrip("/")
        json_url = clean_url + ".json"

        headers = {"User-Agent": _USER_AGENT}

        # Use OAuth if credentials are configured
        token = None
        if settings.reddit_client_id and settings.reddit_client_secret:
            token = await self._get_oauth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
                # OAuth API uses oauth.reddit.com
                json_url = json_url.replace("www.reddit.com", "oauth.reddit.com")
                json_url = json_url.replace("old.reddit.com", "oauth.reddit.com")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                json_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "json" not in content_type and "text/html" in content_type:
                    raise RuntimeError("Reddit returned HTML instead of JSON (likely blocked)")
                resp.raise_for_status()
                data = await resp.json(content_type=None)

        post = data[0]["data"]["children"][0]["data"]
        title = post.get("title", "")
        selftext = post.get("selftext", "")
        author = post.get("author")

        media_items: list[MediaItem] = []

        # Check for image
        if post.get("post_hint") == "image":
            media_items.append(MediaItem(url=post["url"], media_type=MediaType.IMAGE))
        # Check for hosted video — download via yt-dlp for audio+video merge
        elif post.get("is_video") and post.get("media"):
            try:
                dl = await ytdlp_download(url)
                if dl.data:
                    item = MediaItem(url=url, media_type=MediaType.VIDEO)
                    item.data = dl.data
                    media_items.append(item)
            except RuntimeError:
                # Fallback to direct URL (no audio)
                video_url = post["media"]["reddit_video"]["fallback_url"]
                media_items.append(MediaItem(url=video_url, media_type=MediaType.VIDEO))
        # Check for gallery
        elif post.get("is_gallery") and post.get("media_metadata"):
            for _media_id, meta in post["media_metadata"].items():
                if meta.get("status") == "valid" and meta.get("s", {}).get("u"):
                    img_url = meta["s"]["u"].replace("&amp;", "&")
                    media_items.append(MediaItem(url=img_url, media_type=MediaType.IMAGE))
        # Check for external link with preview image
        elif post.get("post_hint") == "link" and post.get("preview"):
            images = post["preview"].get("images", [])
            if images:
                img_url = images[0]["source"]["url"].replace("&amp;", "&")
                media_items.append(MediaItem(url=img_url, media_type=MediaType.IMAGE))

        caption = title
        if selftext:
            caption = f"{title}\n\n{selftext}"

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=f"u/{author}" if author else None,
            caption=caption,
            media_items=media_items,
        )

    async def _resolve_shortlink(self, url: str) -> str:
        """Resolve Reddit /s/ share shortlinks by following redirects.

        URLs like https://www.reddit.com/r/sub/s/ABC123 are redirect shortlinks
        that resolve to the actual post URL.
        """
        if "/s/" not in url:
            return url

        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(
                    url,
                    headers={"User-Agent": _USER_AGENT},
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resolved = str(resp.url)
                    # Strip query params from resolved URL
                    resolved = resolved.split("?")[0]
                    logger.info("reddit_shortlink_resolved", original=url, resolved=resolved)
                    return resolved
        except Exception as exc:
            logger.warning("reddit_shortlink_resolve_failed", url=url, error=str(exc))
            return url

    async def _get_oauth_token(self) -> str | None:
        """Get an OAuth token using client credentials flow."""
        try:
            auth = aiohttp.BasicAuth(settings.reddit_client_id, settings.reddit_client_secret)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://www.reddit.com/api/v1/access_token",
                    auth=auth,
                    data={"grant_type": "client_credentials"},
                    headers={"User-Agent": _USER_AGENT},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("access_token")
        except Exception as exc:
            logger.warning("reddit_oauth_failed", error=str(exc))
        return None

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        """Fallback to yt-dlp for Reddit videos."""
        result = await ytdlp_download(url)
        if not result.data:
            raise RuntimeError("yt-dlp downloaded no data for Reddit URL")

        item = MediaItem(url=url, media_type=MediaType.VIDEO)
        item.data = result.data

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=result.uploader,
            caption=result.title,
            media_items=[item],
        )
