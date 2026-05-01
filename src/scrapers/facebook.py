from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import aiohttp
import structlog

from src.config import settings
from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform
from src.utils.ytdlp import ytdlp_download

logger = structlog.get_logger()

_CURL_USER_AGENT = "curl/7.68.0"

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Full browser-like headers that Facebook checks to distinguish real browsers from bots
_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": _BROWSER_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    # Brotli intentionally omitted — aiohttp can't decode 'br' without the Brotli
    # package installed, and FB serves brotli when 'br' is advertised, which
    # killed the og:image phase with 'Can not decode content-encoding: brotli'.
    # gzip is plenty for HTML.
    "Accept-Encoding": "gzip, deflate",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Facebook tracking params to strip from resolved URLs
_FB_TRACKING_PARAMS = {"rdid", "share_url", "refsrc", "_rdr", "__tn__", "ref", "mibextid"}

# Markers that indicate the start of related-post / suggestion / comment sections
# on mbasic permalink pages and the FB embed plugin. Cutting HTML at the earliest
# match keeps later image regexes from grabbing neighbour-post images that share
# the scontent.fbcdn.net domain (the user-reported "wrong-image" bug).
_FB_RELATED_BOUNDARIES: tuple[str, ...] = (
    r'<div\s+id=["\']composer_root',
    r'<div\s+id=["\']see_next',
    r'<div\s+id=["\']m_more_about',
    r"\bMore from this Page\b",
    r"\bRelated (?:videos|posts|content)\b",
    r"\bSuggested for You\b",
    r"\bPeople You May Know\b",
    r"\bSee more on Facebook\b",
    r"\bMost relevant\b",
)


def _truncate_at_related_content(html: str) -> str:
    """Cut HTML at the earliest related-content / comments marker.

    mbasic and the FB embed plugin render the target post first, then sections
    that share the same scontent.fbcdn.net domain — running broad image regexes
    over the whole document conflates the two. This narrows the haystack.
    """
    earliest = len(html)
    for pattern in _FB_RELATED_BOUNDARIES:
        m = re.search(pattern, html, re.IGNORECASE)
        if m and m.start() < earliest:
            earliest = m.start()
    return html[:earliest]


def _extract_author_from_html(html: str) -> str | None:
    """Extract the post author from JSON-LD, mbasic header, or og:title.

    Falls back through three sources because Facebook serves different markup
    depending on the surface (mbasic vs www vs embed plugin).
    """
    for m in re.finditer(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            author = item.get("author")
            if isinstance(author, dict):
                name = author.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()
            elif isinstance(author, str) and author.strip():
                return author.strip()

    # mbasic: <h3><strong><a href="/profile">Name</a></strong></h3>
    m = re.search(
        r'<h3[^>]*>\s*<strong[^>]*>\s*<a[^>]+href="/[^"]*"[^>]*>([^<]+)</a>',
        html,
        re.IGNORECASE,
    )
    if m:
        name = m.group(1).strip()
        if name:
            return name

    # og:title — often just the author name (or "Name | Facebook")
    m = re.search(
        r'<meta\s+[^>]*?property=["\']og:title["\'][^>]*?content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        title = re.sub(r"\s*[|\-]\s*Facebook\s*$", "", m.group(1).strip(), flags=re.IGNORECASE)
        if title:
            return title

    return None


def _dbg(event: str, **kwargs: object) -> None:
    """Log at info level when debug_mode is on, otherwise debug."""
    if settings.debug_mode:
        logger.info(event, **kwargs)
    else:
        logger.debug(event, **kwargs)


def _clean_facebook_url(url: str) -> str:
    """Strip Facebook tracking/share query params that break scraping."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    clean_query = {k: v for k, v in parse_qs(parsed.query).items() if k not in _FB_TRACKING_PARAMS}
    cleaned = parsed._replace(query=urlencode(clean_query, doseq=True) if clean_query else "")
    return urlunparse(cleaned)


def _read_cookies_for_domain(cookies_file: str, domain: str) -> str | None:
    """Read cookies from a Netscape-format cookies.txt for a specific domain.

    Returns a Cookie header string like "name1=value1; name2=value2".
    """
    from pathlib import Path

    cookies_path = Path(cookies_file)
    if not cookies_path.exists():
        return None

    cookies = []
    for line in cookies_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and domain in parts[0]:
            cookies.append(f"{parts[5]}={parts[6]}")

    return "; ".join(cookies) if cookies else None


class FacebookScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.FACEBOOK

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Download Facebook media via multi-phase fallback chain.

        Chain: yt-dlp → fdown → facebook-scraper → og:image → embed → mbasic.
        gallery-dl is NOT used because it downloads entire page feeds.
        """
        _dbg("fb_extract_start", url=url)

        # Resolve /share/ shortlinks upfront so all methods get the real URL
        url = await self._resolve_share_link(url)
        _dbg("fb_resolved_url", url=url)

        # Phase 1: yt-dlp — handles video posts
        try:
            _dbg("fb_phase1_ytdlp", url=url)
            result = await ytdlp_download(url, cookies_file=settings.cookies_file)
            if result.data:
                _dbg("fb_phase1_ytdlp_ok", size=len(result.data), is_video=result.is_video)
                if result.is_animation:
                    media_type = MediaType.ANIMATION
                elif result.is_video:
                    media_type = MediaType.VIDEO
                else:
                    media_type = MediaType.IMAGE
                item = MediaItem(url=url, media_type=media_type)
                item.data = result.data
                return ScrapedMedia(
                    platform=self.platform,
                    original_url=url,
                    author=result.uploader,
                    caption=result.description or result.title,
                    media_items=[item],
                )
            _dbg("fb_phase1_ytdlp_no_data", url=url)
        except RuntimeError as exc:
            _dbg("fb_phase1_ytdlp_failed", url=url, error=str(exc))

        # Phase 2: fdown — video fallback
        try:
            _dbg("fb_phase2_fdown", url=url)
            return await self._fdown_fallback(url)
        except Exception as exc:
            _dbg("fb_phase2_fdown_failed", url=url, error=str(exc))

        # Phase 3: facebook-scraper library (handles both images and videos)
        try:
            _dbg("fb_phase3_fbscraper", url=url)
            return await self._fbscraper_fallback(url)
        except Exception as exc:
            _dbg("fb_phase3_fbscraper_failed", url=url, error=str(exc))

        # Phase 4: og:image from www.facebook.com
        try:
            _dbg("fb_phase4_opengraph", url=url)
            return await self._opengraph_fallback(url)
        except Exception as exc:
            _dbg("fb_phase4_opengraph_failed", url=url, error=str(exc))

        # Phase 5: Facebook embed plugin (no auth required, public posts only)
        try:
            _dbg("fb_phase5_embed", url=url)
            return await self._embed_fallback(url)
        except Exception as exc:
            _dbg("fb_phase5_embed_failed", url=url, error=str(exc))

        # Phase 6: mbasic — last resort image extraction
        _dbg("fb_phase6_mbasic", url=url)
        return await self._mbasic_fallback(url)

    async def _fbscraper_fallback(self, url: str) -> ScrapedMedia:
        """Extract post media using the facebook-scraper library.

        facebook-scraper parses Facebook's mobile HTML to extract images,
        videos, text and metadata. It's synchronous, so we run it in a thread.
        """
        from facebook_scraper import get_posts

        def _scrape() -> dict:
            cookies = settings.cookies_file if settings.cookies_file else None
            posts = get_posts(
                post_urls=[url],
                cookies=cookies,
                options={"allow_extra_requests": False},
            )
            return next(posts)

        post = await asyncio.to_thread(_scrape)

        _dbg(
            "fb_fbscraper_post",
            has_images=bool(post.get("images")),
            has_image=bool(post.get("image")),
            has_video=bool(post.get("video")),
            has_text=bool(post.get("text")),
            username=post.get("username"),
        )

        # Collect image URLs
        image_urls: list[str] = []
        if post.get("images"):
            image_urls.extend(post["images"])
        elif post.get("image"):
            image_urls.append(post["image"])

        video_url = post.get("video")

        if not image_urls and not video_url:
            raise RuntimeError("facebook-scraper returned no media")

        media_items: list[MediaItem] = []

        # Download video if present
        if video_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        video_url,
                        headers={"User-Agent": _BROWSER_USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.read()
                        if data and len(data) > 10_000:
                            item = MediaItem(url=video_url, media_type=MediaType.VIDEO)
                            item.data = data
                            media_items.append(item)
                            _dbg("fb_fbscraper_video_ok", size=len(data))
            except Exception as exc:
                _dbg("fb_fbscraper_video_failed", error=str(exc))

        # Download images
        for img_url in image_urls[:10]:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        img_url,
                        headers={"User-Agent": _BROWSER_USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.read()
                        if len(data) < 5_000:
                            _dbg("fb_fbscraper_img_small", size=len(data))
                            continue
                item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                item.data = data
                media_items.append(item)
                _dbg("fb_fbscraper_img_ok", size=len(data))
            except Exception as exc:
                _dbg("fb_fbscraper_img_failed", error=str(exc))

        if not media_items:
            raise RuntimeError("facebook-scraper found URLs but downloads failed")

        caption = post.get("text")
        author = post.get("username")

        _dbg(
            "fb_fbscraper_success",
            media_count=len(media_items),
            has_caption=caption is not None,
        )

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
            caption=caption,
            media_items=media_items,
        )

    async def _opengraph_fallback(self, url: str) -> ScrapedMedia:
        """Extract post image via og:image meta tag from www.facebook.com."""
        headers = dict(_BROWSER_HEADERS)

        # Load cookies
        has_cookies = False
        if settings.cookies_file:
            try:
                cookie_header = _read_cookies_for_domain(settings.cookies_file, "facebook.com")
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    has_cookies = True
                    _dbg("fb_og_cookies_loaded", cookie_count=cookie_header.count(";") + 1)
                else:
                    _dbg("fb_og_no_cookies_for_domain")
            except Exception as exc:
                _dbg("fb_og_cookie_read_error", error=str(exc))
        else:
            _dbg("fb_og_no_cookies_file")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                final_url = str(resp.url)
                _dbg(
                    "fb_og_response",
                    status=resp.status,
                    final_url=final_url,
                    has_cookies=has_cookies,
                    redirected=final_url != url,
                )

                # Check if we got redirected to login
                if "/login" in final_url:
                    raise RuntimeError(f"Redirected to login page: {final_url}")

                resp.raise_for_status()
                html = await resp.text(encoding="utf-8", errors="ignore")
                html = html[:100_000]

        html_len = len(html)
        _dbg("fb_og_html_received", length=html_len)

        # Log a snippet of the HTML head for debugging
        if settings.debug_mode:
            # Extract <head>...</head> or first 2000 chars
            head_match = re.search(r"<head[^>]*>(.*?)</head>", html, re.DOTALL | re.IGNORECASE)
            head_snippet = head_match.group(1)[:2000] if head_match else html[:2000]
            # Find all meta tags with "og:" to show what's available
            og_tags = re.findall(r"<meta\s+[^>]*?og:[^>]+>", html, re.IGNORECASE)
            _dbg(
                "fb_og_html_head",
                og_tags_found=len(og_tags),
                og_tags=og_tags[:10],
                head_length=len(head_snippet),
                title_in_html="<title" in html.lower(),
            )

        # Extract og:image
        og_match = re.search(
            r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if not og_match:
            og_match = re.search(
                r'<meta\s+[^>]*?content=["\']([^"\']+)["\'][^>]*?property=["\']og:image["\']',
                html,
                re.IGNORECASE,
            )
        if not og_match:
            _dbg(
                "fb_og_no_image_tag",
                has_any_meta=bool(re.search(r"<meta\s", html, re.IGNORECASE)),
                has_any_og=bool(re.search(r"og:", html, re.IGNORECASE)),
                login_page="/login" in html.lower(),
                checkpoint="checkpoint" in html.lower(),
            )
            raise RuntimeError("No og:image found on Facebook page")

        image_url = og_match.group(1).replace("&amp;", "&")
        _dbg("fb_og_image_found", image_url=image_url[:200])

        # Download the image
        async with aiohttp.ClientSession() as session:
            async with session.get(
                image_url,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.read()

        _dbg("fb_og_image_downloaded", size=len(data) if data else 0)

        if not data or len(data) < 5_000:
            raise RuntimeError(f"og:image download too small ({len(data) if data else 0} bytes)")

        item = MediaItem(url=image_url, media_type=MediaType.IMAGE)
        item.data = data

        # Caption: prefer og:description (post body) over og:title (often author name)
        desc_match = re.search(
            r'<meta\s+[^>]*?property=["\']og:description["\'][^>]*?content=["\']([^"\']*)["\']',
            html,
            re.IGNORECASE,
        )
        caption = None
        if desc_match and desc_match.group(1):
            caption = desc_match.group(1).replace("&amp;", "&")

        author = _extract_author_from_html(html)

        _dbg(
            "fb_og_success",
            caption_length=len(caption) if caption else 0,
            has_author=author is not None,
        )

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
            caption=caption,
            media_items=[item],
        )

    async def _embed_fallback(self, url: str) -> ScrapedMedia:
        """Extract post image via Facebook's embed plugin page.

        Facebook's /plugins/post.php renders public post content in a lightweight
        iframe-friendly page. It doesn't require authentication and serves og:image
        meta tags for posts with images.
        """
        embed_url = (
            f"https://www.facebook.com/plugins/post.php?href={quote(url, safe='')}"
            "&show_text=true&width=500"
        )
        _dbg("fb_embed_fetching", embed_url=embed_url)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                embed_url,
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                final_url = str(resp.url)
                _dbg("fb_embed_response", status=resp.status, final_url=final_url)
                resp.raise_for_status()
                html = await resp.text(encoding="utf-8", errors="ignore")
                html = html[:100_000]

        _dbg("fb_embed_html_received", length=len(html))

        # Scope to the embedded post: drop everything past the related-content marker.
        # Why: the embed plugin used to render strictly one post, but FB now injects
        # 'See more on Facebook' / suggestions whose images live on the same CDN —
        # the old broad data-src/background-image regexes pulled neighbour images
        # in alongside (or instead of) the target post's image (DAN-65).
        post_html = _truncate_at_related_content(html)
        _dbg("fb_embed_scoped", before=len(html), after=len(post_html))

        # Only trust narrow signals: og:image (canonical post image) and
        # scaledImageFitWidth (FB's class for actual post photos in the embed).
        image_urls: list[str] = []
        for match in re.finditer(
            r'<meta\s+[^>]*?property=["\']og:image["\'][^>]*?content=["\']([^"\']+)["\']',
            post_html,
            re.IGNORECASE,
        ):
            image_urls.append(match.group(1).replace("&amp;", "&"))

        for match in re.finditer(
            r'<img[^>]+class="[^"]*scaledImageFitWidth[^"]*"[^>]+src="([^"]+)"',
            post_html,
            re.IGNORECASE,
        ):
            image_urls.append(match.group(1).replace("&amp;", "&"))

        seen: set[str] = set()
        unique_urls: list[str] = []
        for img_url in image_urls:
            if img_url not in seen:
                seen.add(img_url)
                unique_urls.append(img_url)

        _dbg("fb_embed_images_found", count=len(unique_urls))

        if not unique_urls:
            if settings.debug_mode:
                _dbg(
                    "fb_embed_no_images_html",
                    html_snippet=html[:3000],
                    has_img_tags=bool(re.search(r"<img\s", html, re.IGNORECASE)),
                )
            raise RuntimeError("No images found in Facebook embed page")

        # Download image(s)
        media_items: list[MediaItem] = []
        for img_url in unique_urls[:5]:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        img_url,
                        headers={"User-Agent": _BROWSER_USER_AGENT},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        resp.raise_for_status()
                        data = await resp.read()
                        if len(data) < 5_000:
                            _dbg("fb_embed_image_too_small", size=len(data))
                            continue
                item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                item.data = data
                media_items.append(item)
                _dbg("fb_embed_image_ok", size=len(data))
            except Exception as exc:
                _dbg("fb_embed_image_download_failed", error=str(exc))

        if not media_items:
            raise RuntimeError("Failed to download any images from Facebook embed page")

        # Caption: prefer the rendered post body (._5pbx) over og:description
        caption = None
        text_match = re.search(
            r'<div[^>]+class="[^"]*_5pbx[^"]*"[^>]*>(.*?)</div>',
            post_html,
            re.DOTALL | re.IGNORECASE,
        )
        if text_match:
            raw = re.sub(r"<[^>]+>", "", text_match.group(1)).strip()
            if raw:
                caption = raw.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

        if not caption:
            desc_match = re.search(
                r'<meta\s+[^>]*?property=["\']og:description["\'][^>]*?'
                r'content=["\']([^"\']*)["\']',
                post_html,
                re.IGNORECASE,
            )
            if desc_match and desc_match.group(1):
                caption = desc_match.group(1).replace("&amp;", "&")

        author = _extract_author_from_html(post_html)

        _dbg(
            "fb_embed_success",
            images=len(media_items),
            has_caption=caption is not None,
            has_author=author is not None,
        )

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
            caption=caption,
            media_items=media_items,
        )

    async def _resolve_share_link(self, url: str) -> str:
        """Resolve Facebook /share/ shortlinks by following redirects.

        URLs like https://www.facebook.com/share/p/ABC123/ are shortlinks
        that 302-redirect to the actual post URL.

        Strategy:
        1. GET www.facebook.com/share/... with allow_redirects=False to catch 302
        2. If that fails, GET mbasic with allow_redirects=True and extract from
           the login page ?next= param (unauthenticated fallback)
        """
        if "/share/" not in url:
            return url

        _dbg("fb_share_resolving", url=url)

        headers: dict[str, str] = {"User-Agent": _CURL_USER_AGENT}
        if settings.cookies_file:
            try:
                cookie_header = _read_cookies_for_domain(settings.cookies_file, "facebook.com")
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    _dbg("fb_share_cookies_loaded")
            except Exception:
                pass

        # Strategy 1: Catch the 302 Location header from www.facebook.com
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    _dbg(
                        "fb_share_strategy1",
                        status=resp.status,
                        location=resp.headers.get("Location", ""),
                    )
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location", "")
                        if location and "/share/" not in location and "/login" not in location:
                            location = _clean_facebook_url(location)
                            logger.info(
                                "facebook_share_resolved",
                                original=url,
                                resolved=location,
                            )
                            return location
                        _dbg(
                            "fb_share_strategy1_rejected",
                            location=location,
                            has_share="/share/" in location if location else False,
                            has_login="/login" in location if location else False,
                        )
        except Exception as exc:
            _dbg("fb_share_strategy1_error", url=url, error=str(exc))

        # Strategy 2: Follow mbasic redirects — even if it hits login, extract ?next=
        mbasic_url = re.sub(
            r"https?://(?:www\.|m\.)?facebook\.com",
            "https://mbasic.facebook.com",
            url,
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    mbasic_url,
                    headers={"User-Agent": _CURL_USER_AGENT},
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    resolved = str(resp.url)
                    _dbg("fb_share_strategy2", final_url=resolved, status=resp.status)

                    if "/login" in resolved:
                        parsed = urlparse(resolved)
                        next_params = parse_qs(parsed.query).get("next", [])
                        _dbg("fb_share_strategy2_login", next_params=next_params)
                        if next_params:
                            resolved = next_params[0]
                    # Convert mbasic back to www for yt-dlp/gallery-dl compatibility
                    resolved = re.sub(
                        r"https?://mbasic\.facebook\.com",
                        "https://www.facebook.com",
                        resolved,
                    )
                    if resolved != url and "/share/" not in resolved:
                        resolved = _clean_facebook_url(resolved)
                        logger.info(
                            "facebook_share_resolved",
                            original=url,
                            resolved=resolved,
                        )
                        return resolved
                    _dbg("fb_share_strategy2_no_change", resolved=resolved)
        except Exception as exc:
            _dbg("fb_share_strategy2_error", url=url, error=str(exc))

        logger.warning("facebook_share_unresolved", url=url)
        return url

    async def _mbasic_fallback(self, url: str) -> ScrapedMedia:
        """Extract images from Facebook's basic mobile site.

        mbasic.facebook.com serves lightweight HTML that often includes
        direct image URLs even without authentication.
        """
        # Convert URL to mbasic.facebook.com (share links already resolved in _primary_extract)
        mbasic_url = re.sub(
            r"https?://(?:www\.|m\.)?facebook\.com",
            "https://mbasic.facebook.com",
            url,
        )

        headers: dict[str, str] = {"User-Agent": _CURL_USER_AGENT}
        if settings.cookies_file:
            try:
                cookie_header = _read_cookies_for_domain(settings.cookies_file, "facebook.com")
                if cookie_header:
                    headers["Cookie"] = cookie_header
            except Exception as exc:
                _dbg("fb_mbasic_cookie_read_failed", error=str(exc))

        async with aiohttp.ClientSession() as session:
            async with session.get(
                mbasic_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                final_url = str(resp.url)
                _dbg("fb_mbasic_response", status=resp.status, final_url=final_url)
                resp.raise_for_status()
                html = await resp.text()

        # mbasic happily 200s a login page when cookies are missing/stale, then
        # the only "images" we find are static.xx.fbcdn.net UI sprites — that
        # bubbles up as the misleading "Failed to download any images" error.
        # Bail explicitly so the failure is diagnosable.
        if "/login" in final_url or "checkpoint" in final_url:
            raise RuntimeError(f"mbasic redirected to login/checkpoint: {final_url}")

        _dbg("fb_mbasic_html_received", length=len(html))

        # og:image lives in <head> and always points at the target post — pull it
        # before truncating, since boundary markers can appear before </head>.
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

        # Scope CDN-image regex to the post body. mbasic permalink pages render
        # comments + 'More from this Page' / 'Related videos' below the post on
        # the same CDN domain — broad regex over the whole document grabbed
        # neighbour-post images and reposted them as if they were the target
        # (DAN-65 user-reported wrong-image bug).
        post_html = _truncate_at_related_content(html)
        _dbg("fb_mbasic_scoped", before=len(html), after=len(post_html))

        image_urls = re.findall(
            r'(?:src|data-src|srcset)=["\']'
            r"(.*?(?:scontent|external|fbcdn).*?(?:jpg|jpeg|png|webp))"
            r'["\']',
            post_html,
            re.IGNORECASE,
        )

        _dbg(
            "fb_mbasic_urls_found",
            cdn_images=len(image_urls),
            og_images=len(og_images),
        )

        # Combine and deduplicate, preferring larger images
        all_urls = og_images + image_urls  # og:image first (usually higher quality)
        seen = set()
        unique_urls = []
        for img_url in all_urls:
            # Unescape HTML entities
            img_url = img_url.replace("&amp;", "&")
            # static.xx.fbcdn.net serves FB's chrome (icons, sprites, JS shards)
            # — never post media. Filtering here avoids burning the per-URL
            # download budget on guaranteed-too-small responses.
            if "static.xx.fbcdn.net" in img_url:
                continue
            if img_url not in seen:
                seen.add(img_url)
                unique_urls.append(img_url)

        if not unique_urls:
            if settings.debug_mode:
                # Log HTML snippet to help diagnose what mbasic returned
                _dbg(
                    "fb_mbasic_no_images_html",
                    html_snippet=html[:3000],
                    login_page="/login" in html.lower(),
                    checkpoint="checkpoint" in html.lower(),
                )
            raise RuntimeError("Could not find any images on Facebook mbasic page")

        # Caption: prefer og:description (post body); og:title is usually the author name
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
                            _dbg("fb_mbasic_image_too_small", url=img_url, size=len(data))
                            continue
                item = MediaItem(url=img_url, media_type=MediaType.IMAGE)
                item.data = data
                media_items.append(item)
                _dbg("fb_mbasic_image_ok", url=img_url[:100], size=len(data))
            except Exception as exc:
                logger.warning("facebook_image_download_failed", url=img_url, error=str(exc))

        if not media_items:
            raise RuntimeError("Failed to download any images from Facebook post")

        caption = desc_match.group(1) if desc_match else None
        author = _extract_author_from_html(html)

        _dbg("fb_mbasic_success", images=len(media_items), has_author=author is not None)

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=author,
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
