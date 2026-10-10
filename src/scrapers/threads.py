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

_THREADS_HOSTS = {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}
_EXPECTED_URL = "https://threads.{net,com}/@user/post/<code> or /t/<code>"

_POST_PATH = re.compile(r"/(?:@[\w.]+/post/|t/)([A-Za-z0-9_-]+)/?$")
# Threads serves public post data to link-preview crawlers; a browser UA gets
# a login page without post JSON or Open Graph metadata.
_USER_AGENT = "facebookexternalhit/1.1"


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
        match = self._validate_url(url)
        page = await self._fetch_page(url)
        result = self._from_page_data(page, match.group(1), url)
        return result if result is not None else self._opengraph_fallback(page, url)

    @staticmethod
    def _validate_url(url: str) -> re.Match[str]:
        parsed = urlparse(url)
        match = _POST_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in _THREADS_HOSTS
            or not match
        ):
            raise RuntimeError(f"Invalid Threads post URL: {url!r} (expected {_EXPECTED_URL})")
        return match

    @staticmethod
    async def _fetch_page(url: str) -> _ThreadsPage:
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
        return page

    def _from_page_data(self, page: _ThreadsPage, shortcode: str, url: str) -> ScrapedMedia | None:
        for script in page.scripts:
            try:
                payload = json.loads(script)
            except json.JSONDecodeError:
                continue
            post = _find_post(payload, shortcode)
            if post is None:
                continue
            return self._from_post(post, url)
        return None

    def _from_post(self, post: dict, url: str) -> ScrapedMedia:
        caption = (post.get("caption") or {}).get("text")
        items = _media_items(post)
        if not caption and not items:
            raise RuntimeError(
                f"Threads post has no accessible content: {url!r} (expected text or media)"
            )
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=escape(post["user"].get("username") or "") or None,
            caption=escape(caption) if caption else None,
            media_items=items,
        )

    def _opengraph_fallback(self, page: _ThreadsPage, url: str) -> ScrapedMedia:
        # Require a post permalink so login/error pages and the site logo cannot
        # be mistaken for the requested post when public data is unavailable.
        canonical = urlparse(page.meta.get("og:url", ""))
        canonical_match = _POST_PATH.fullmatch(canonical.path)
        requested_match = _POST_PATH.fullmatch(urlparse(url).path)
        if canonical.hostname not in _THREADS_HOSTS or not canonical_match:
            raise RuntimeError(
                f"Threads page contains no public post data: {url!r} "
                f"(expected og:url matching {_EXPECTED_URL})"
            )
        if not requested_match or canonical_match.group(1) != requested_match.group(1):
            raise RuntimeError(
                f"Threads page metadata belongs to another post: {url!r} "
                "(expected the requested shortcode)"
            )

        video = page.meta.get("og:video") or page.meta.get("og:video:url")
        # og:image can be the author's avatar, even on a text-only post.
        # Only structured post data can reliably identify image attachments.
        items = [MediaItem(video, MediaType.VIDEO)] if _is_http(video) else []
        caption = page.meta.get("og:description")
        if not caption and not items:
            raise RuntimeError(
                f"Threads page contains no accessible content: {url!r} (expected text or media)"
            )
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=escape(canonical.path.split("/")[1][1:])
            if canonical.path.startswith("/@")
            else None,
            caption=escape(caption) if caption else None,
            media_items=items,
        )
