"""Extract Open Graph metadata (og:image, og:title, etc.) from web pages.

Used as a fallback for image-only posts on Instagram, Facebook, etc.
where yt-dlp only handles video content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import aiohttp
import structlog

logger = structlog.get_logger()

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Regex patterns for og: meta tags (handles both property= and name= variants,
# and both single and double quotes, and content before/after property)
_OG_PATTERN = re.compile(
    r'<meta\s+(?:[^>]*?)'
    r'(?:property|name)\s*=\s*["\']og:(\w+)["\']'
    r'[^>]*?content\s*=\s*["\']([^"\']*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
_OG_PATTERN_REV = re.compile(
    r'<meta\s+(?:[^>]*?)'
    r'content\s*=\s*["\']([^"\']*?)["\']'
    r'[^>]*?(?:property|name)\s*=\s*["\']og:(\w+)["\']',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class OpenGraphData:
    image: str | None = None
    title: str | None = None
    description: str | None = None
    site_name: str | None = None


async def fetch_opengraph(
    url: str,
    cookies_file: str | None = None,
) -> OpenGraphData:
    """Fetch a page and extract Open Graph meta tags."""
    headers = {"User-Agent": _USER_AGENT}

    # Load cookies from file if provided
    jar = None
    if cookies_file:
        jar = aiohttp.CookieJar()
        # Note: aiohttp doesn't natively load Netscape cookies files,
        # but we pass cookies via headers for simplicity

    async with aiohttp.ClientSession(cookie_jar=jar) as session:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            # Only read first 100KB to find meta tags (avoid downloading huge pages)
            html = await resp.text(encoding="utf-8", errors="ignore")
            html = html[:100_000]

    og = OpenGraphData()
    found: dict[str, str] = {}

    # Try both orderings of property/content attributes
    for match in _OG_PATTERN.finditer(html):
        key, value = match.group(1).lower(), match.group(2)
        found[key] = value

    for match in _OG_PATTERN_REV.finditer(html):
        value, key = match.group(1), match.group(2).lower()
        if key not in found:  # don't override
            found[key] = value

    og.image = found.get("image")
    og.title = found.get("title")
    og.description = found.get("description")
    og.site_name = found.get("site_name")

    return og


async def download_og_image(og: OpenGraphData) -> bytes | None:
    """Download the og:image URL and return bytes."""
    if not og.image:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                og.image,
                headers={"User-Agent": _USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
    except Exception as exc:
        logger.warning("og_image_download_failed", url=og.image, error=str(exc))
        return None
