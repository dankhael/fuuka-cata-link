from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


class Platform(StrEnum):
    TWITTER = "twitter"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"
    GITHUB = "github"
    REDDIT = "reddit"


@dataclass(frozen=True)
class DetectedLink:
    url: str
    platform: Platform
    is_spoiler: bool = False


# Patterns for each platform. Order matters — first match wins for a given URL.
_PLATFORM_PATTERNS: list[tuple[Platform, re.Pattern[str]]] = [
    (
        Platform.TWITTER,
        re.compile(
            r"https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+/status/\d+",
            re.IGNORECASE,
        ),
    ),
    (
        Platform.YOUTUBE,
        re.compile(
            r"https?://(?:www\.)?(?:youtube\.com/shorts/|youtu\.be/)\S+",
            re.IGNORECASE,
        ),
    ),
    (
        Platform.INSTAGRAM,
        re.compile(
            r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels)/\S+",
            re.IGNORECASE,
        ),
    ),
    (
        # Match vt.tiktok.com, vm.tiktok.com, www.tiktok.com, tiktok.com
        Platform.TIKTOK,
        re.compile(
            r"https?://(?:(?:www|vm|vt)\.)?tiktok\.com/\S+",
            re.IGNORECASE,
        ),
    ),
    (
        Platform.FACEBOOK,
        re.compile(
            r"https?://(?:www\.|m\.)?facebook\.com/\S+",
            re.IGNORECASE,
        ),
    ),
    (
        # Match both /commit/ and /pull/ URLs
        Platform.GITHUB,
        re.compile(
            r"https?://(?:www\.)?github\.com/[\w\-]+/[\w\-]+/(?:commit/[0-9a-f]+|pull/\d+)",
            re.IGNORECASE,
        ),
    ),
    (
        Platform.REDDIT,
        re.compile(
            r"https?://(?:www\.|old\.)?reddit\.com/r/\S+",
            re.IGNORECASE,
        ),
    ),
]


def _clean_url(url: str, platform: Platform) -> str:
    """Strip tracking/share query params that break scraping."""
    parsed = urlparse(url)

    if platform == Platform.REDDIT:
        # Reddit share links add utm_source, utm_medium etc. — strip them all
        clean_query = {
            k: v for k, v in parse_qs(parsed.query).items()
            if not k.startswith("utm_") and k not in ("share", "context")
        }
        cleaned = parsed._replace(
            query=urlencode(clean_query, doseq=True) if clean_query else ""
        )
        return urlunparse(cleaned)

    if platform == Platform.TIKTOK:
        # Strip query params like ?q=...&t=... that can break yt-dlp
        cleaned = parsed._replace(query="")
        return urlunparse(cleaned)

    return url


def detect_links(text: str) -> list[DetectedLink]:
    """Extract all supported social media links from a text message."""
    results: list[DetectedLink] = []
    seen_urls: set[str] = set()

    for platform, pattern in _PLATFORM_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(0).rstrip(".,;:!?)\"'")
            url = _clean_url(url, platform)
            if url not in seen_urls:
                seen_urls.add(url)
                results.append(DetectedLink(url=url, platform=platform))

    return results
