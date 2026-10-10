"""Extract public Threads posts from their server-rendered page data."""

from __future__ import annotations

import json
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

import aiohttp

from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform

_POST_PATH = re.compile(r"/(?:@[\w.]+/post/|t/)([A-Za-z0-9_-]+)/?$")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class _ThreadsPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self._json_script = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            self._json_script = attributes.get("type") == "application/json"
            self._parts = []
        if tag == "meta":
            key = attributes.get("property") or attributes.get("name") or ""
            if key.startswith("og:") and attributes.get("content"):
                self.meta.setdefault(key, attributes["content"])

    def handle_data(self, data: str) -> None:
        if self._json_script:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_script:
            self.scripts.append("".join(self._parts))
            self._json_script = False


def _find_post(payload: object, shortcode: str) -> dict | None:
    # Match the linked post, never a reply, recommendation or quoted attachment.
    pending = [payload]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if node.get("code") == shortcode and isinstance(node.get("user"), dict):
                return node
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return None


def _best_url(versions: list[dict]) -> str | None:
    usable = [v for v in versions if isinstance(v, dict) and _is_http(v.get("url"))]
    if not usable:
        return None
    return max(usable, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0))["url"]


def _is_http(url: object) -> bool:
    return isinstance(url, str) and urlparse(url).scheme in ("http", "https")


def _media_items(post: dict) -> list[MediaItem]:
    items: list[MediaItem] = []
    seen: set[str] = set()
    for attachment in (post.get("carousel_media") or [post])[:10]:
        video_url = _best_url(attachment.get("video_versions") or [])
        image_url = _best_url((attachment.get("image_versions2") or {}).get("candidates") or [])
        media_url = video_url or image_url
        if media_url and media_url not in seen:
            seen.add(media_url)
            items.append(MediaItem(media_url, MediaType.VIDEO if video_url else MediaType.IMAGE))
    return items


class ThreadsScraper(BaseScraper):
    """Scrape a public post, e.g. ``await scraper.extract(threads_post_url)``."""

    @property
    def platform(self) -> Platform:
        return Platform.THREADS

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        parsed = urlparse(url)
        match = _POST_PATH.fullmatch(parsed.path)
        if (
            parsed.hostname
            not in {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
            or not match
        ):
            raise RuntimeError(f"Invalid Threads post URL: {url!r}")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                page_html = await response.text()

        page = _ThreadsPage()
        page.feed(page_html)
        for script in page.scripts:
            try:
                payload = json.loads(script)
            except json.JSONDecodeError:
                continue
            post = _find_post(payload, match.group(1))
            if post is None:
                continue
            caption = (post.get("caption") or {}).get("text")
            items = _media_items(post)
            if not caption and not items:
                raise RuntimeError(f"Threads post has no accessible content: {url!r}")
            return ScrapedMedia(
                platform=self.platform,
                original_url=url,
                author=escape(post["user"].get("username") or "") or None,
                caption=escape(caption) if caption else None,
                media_items=items,
            )

        return self._opengraph_fallback(page, url)

    def _opengraph_fallback(self, page: _ThreadsPage, url: str) -> ScrapedMedia:
        # Require a post permalink so login/error pages and the site logo cannot
        # be mistaken for the requested post when public data is unavailable.
        canonical = urlparse(page.meta.get("og:url", ""))
        canonical_match = _POST_PATH.fullmatch(canonical.path)
        requested_match = _POST_PATH.fullmatch(urlparse(url).path)
        if (
            canonical.hostname
            not in {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
            or not canonical_match
        ):
            raise RuntimeError(f"Threads page contains no public post data: {url!r}")
        if not requested_match or canonical_match.group(1) != requested_match.group(1):
            raise RuntimeError(f"Threads page metadata belongs to another post: {url!r}")

        video = page.meta.get("og:video") or page.meta.get("og:video:url")
        image = page.meta.get("og:image")
        media_url = video if _is_http(video) else image
        items = []
        if _is_http(media_url):
            items.append(
                MediaItem(media_url, MediaType.VIDEO if media_url == video else MediaType.IMAGE)
            )
        caption = page.meta.get("og:description")
        if not caption and not items:
            raise RuntimeError(f"Threads page contains no accessible content: {url!r}")
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=escape(canonical.path.split("/")[1][1:])
            if canonical.path.startswith("/@")
            else None,
            caption=escape(caption) if caption else None,
            media_items=items,
        )
