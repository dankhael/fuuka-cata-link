from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.scrapers.facebook import (
    FacebookScraper,
    _clean_facebook_url,
    _extract_author_from_html,
    _read_cookies_for_domain,
    _truncate_at_related_content,
)
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult

# Shorthand for patching all phases that should fail in a given test.
_YTDLP_FAIL = patch(
    "src.scrapers.facebook.ytdlp_download",
    new_callable=AsyncMock,
    side_effect=RuntimeError("yt-dlp failed"),
)
_FDOWN_FAIL = patch(
    "src.scrapers.facebook.FacebookScraper._fdown_fallback",
    new_callable=AsyncMock,
    side_effect=RuntimeError("fdown failed"),
)
_FBSCRAPER_FAIL = patch(
    "src.scrapers.facebook.FacebookScraper._fbscraper_fallback",
    new_callable=AsyncMock,
    side_effect=RuntimeError("facebook-scraper failed"),
)
_OG_FAIL = patch(
    "src.scrapers.facebook.FacebookScraper._opengraph_fallback",
    new_callable=AsyncMock,
    side_effect=RuntimeError("og failed"),
)
_EMBED_FAIL = patch(
    "src.scrapers.facebook.FacebookScraper._embed_fallback",
    new_callable=AsyncMock,
    side_effect=RuntimeError("embed failed"),
)


# ---------------------------------------------------------------------------
# yt-dlp success path (video posts / reels)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facebook_video_via_ytdlp():
    """Video post extraction works via yt-dlp."""
    mock_result = YtdlpResult(
        title="Funny video",
        description="Check this out",
        uploader="poster",
        data=b"video_bytes",
        is_video=True,
    )

    with patch(
        "src.scrapers.facebook.ytdlp_download",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        scraper = FacebookScraper()
        result = await scraper._primary_extract("https://www.facebook.com/watch?v=123")

    assert result.platform == Platform.FACEBOOK
    assert result.author == "poster"
    assert result.caption == "Check this out"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    assert result.media_items[0].data == b"video_bytes"


# ---------------------------------------------------------------------------
# facebook-scraper success path (image posts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facebook_image_post_via_fbscraper():
    """Image post falls through yt-dlp/fdown to facebook-scraper."""
    fbscraper_result = ScrapedMedia(
        platform=Platform.FACEBOOK,
        original_url="https://www.facebook.com/photo/123",
        author="photographer",
        caption="Nice photo",
        media_items=[
            MediaItem(
                url="https://scontent.fbcdn.net/img.jpg",
                media_type=MediaType.IMAGE,
                data=b"image_data",
            ),
        ],
    )

    with (
        _YTDLP_FAIL,
        _FDOWN_FAIL,
        patch(
            "src.scrapers.facebook.FacebookScraper._fbscraper_fallback",
            new_callable=AsyncMock,
            return_value=fbscraper_result,
        ),
    ):
        scraper = FacebookScraper()
        result = await scraper._primary_extract("https://www.facebook.com/photo/123")

    assert result.caption == "Nice photo"
    assert result.author == "photographer"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.IMAGE


# ---------------------------------------------------------------------------
# Full fallback chain (all fail until mbasic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facebook_fallback_to_mbasic():
    """When all methods fail, falls through to mbasic."""
    mbasic_result = ScrapedMedia(
        platform=Platform.FACEBOOK,
        original_url="https://www.facebook.com/post/456",
        caption="mbasic caption",
        media_items=[
            MediaItem(
                url="https://scontent.fbcdn.net/img.jpg",
                media_type=MediaType.IMAGE,
                data=b"mbasic_image",
            ),
        ],
    )

    with (
        _YTDLP_FAIL,
        _FDOWN_FAIL,
        _FBSCRAPER_FAIL,
        _OG_FAIL,
        _EMBED_FAIL,
        patch(
            "src.scrapers.facebook.FacebookScraper._mbasic_fallback",
            new_callable=AsyncMock,
            return_value=mbasic_result,
        ),
    ):
        scraper = FacebookScraper()
        result = await scraper._primary_extract("https://www.facebook.com/post/456")

    assert result.caption == "mbasic caption"
    assert len(result.media_items) == 1


# ---------------------------------------------------------------------------
# facebook-scraper unit test (post dict → ScrapedMedia)
# ---------------------------------------------------------------------------


def _make_bytes_response(data: bytes):
    """Create a mock aiohttp response that returns bytes."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_fbscraper_fallback_images():
    """facebook-scraper extracts images and text from a post dict."""
    fake_post = {
        "images": [
            "https://scontent.fbcdn.net/v/photo1.jpg",
            "https://scontent.fbcdn.net/v/photo2.jpg",
        ],
        "image": "https://scontent.fbcdn.net/v/photo1.jpg",
        "video": None,
        "text": "Beautiful sunset",
        "username": "photographer",
    }

    image_data = b"x" * 10_000  # >5KB threshold

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=_make_bytes_response(image_data))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.scrapers.facebook.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=fake_post,
        ),
        patch("aiohttp.ClientSession", return_value=mock_session),
    ):
        scraper = FacebookScraper()
        result = await scraper._fbscraper_fallback("https://www.facebook.com/photo/123")

    assert result.platform == Platform.FACEBOOK
    assert result.author == "photographer"
    assert result.caption == "Beautiful sunset"
    assert len(result.media_items) == 2
    assert all(item.media_type == MediaType.IMAGE for item in result.media_items)


@pytest.mark.asyncio
async def test_fbscraper_fallback_video():
    """facebook-scraper extracts video from a post dict."""
    fake_post = {
        "images": [],
        "image": None,
        "video": "https://video.fbcdn.net/v/reel.mp4",
        "text": "Funny reel",
        "username": "creator",
    }

    video_data = b"v" * 50_000  # >10KB threshold

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=_make_bytes_response(video_data))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.scrapers.facebook.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=fake_post,
        ),
        patch("aiohttp.ClientSession", return_value=mock_session),
    ):
        scraper = FacebookScraper()
        result = await scraper._fbscraper_fallback("https://www.facebook.com/watch?v=456")

    assert result.author == "creator"
    assert result.caption == "Funny reel"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO


@pytest.mark.asyncio
async def test_fbscraper_fallback_no_media():
    """facebook-scraper raises when post has no media."""
    fake_post = {
        "images": [],
        "image": None,
        "video": None,
        "text": "Text only post",
        "username": "user",
    }

    with patch(
        "src.scrapers.facebook.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=fake_post,
    ):
        scraper = FacebookScraper()
        with pytest.raises(RuntimeError, match="no media"):
            await scraper._fbscraper_fallback("https://www.facebook.com/post/789")


# ---------------------------------------------------------------------------
# URL cleaning
# ---------------------------------------------------------------------------


def test_clean_facebook_url_strips_tracking():
    """Tracking params like rdid and share_url are removed."""
    dirty = (
        "https://www.facebook.com/user/posts/123"
        "?rdid=abc&share_url=https%3A%2F%2Ffb.com%2Fshare&refsrc=deprecated"
    )
    cleaned = _clean_facebook_url(dirty)
    assert "rdid" not in cleaned
    assert "share_url" not in cleaned
    assert "refsrc" not in cleaned
    assert "/user/posts/123" in cleaned


def test_clean_facebook_url_preserves_non_tracking():
    """Non-tracking params are kept."""
    url = "https://www.facebook.com/user/posts/123?story_fbid=456"
    cleaned = _clean_facebook_url(url)
    assert "story_fbid=456" in cleaned


def test_clean_facebook_url_no_params():
    """URLs without query params are returned unchanged."""
    url = "https://www.facebook.com/user/posts/123"
    assert _clean_facebook_url(url) == url


# ---------------------------------------------------------------------------
# Cookie reader
# ---------------------------------------------------------------------------


def test_read_cookies_for_domain(tmp_path):
    """Reads Netscape-format cookies for the requested domain."""
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t12345\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tabcdef\n"
        ".google.com\tTRUE\t/\tTRUE\t0\tNID\t999\n"
    )

    result = _read_cookies_for_domain(str(cookies_file), "facebook.com")
    assert result == "c_user=12345; xs=abcdef"


def test_read_cookies_for_domain_missing_file():
    """Returns None when cookies file doesn't exist."""
    result = _read_cookies_for_domain("/nonexistent/cookies.txt", "facebook.com")
    assert result is None


def test_read_cookies_for_domain_no_matching_domain(tmp_path):
    """Returns None when no cookies match the domain."""
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(".google.com\tTRUE\t/\tTRUE\t0\tNID\t999\n")

    result = _read_cookies_for_domain(str(cookies_file), "facebook.com")
    assert result is None


# ---------------------------------------------------------------------------
# Share link resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_share_link_passthrough():
    """Non-share URLs are returned unchanged."""
    scraper = FacebookScraper()
    result = await scraper._resolve_share_link("https://www.facebook.com/user/posts/123")
    assert result == "https://www.facebook.com/user/posts/123"


# ---------------------------------------------------------------------------
# Related-content boundary truncation (regression: wrong-image bug)
# ---------------------------------------------------------------------------


def test_truncate_at_related_content_drops_more_from_page():
    """HTML is cut at 'More from this Page' so neighbour images aren't scanned."""
    html = (
        "<head><meta property='og:image' content='https://scontent.fbcdn.net/post.jpg'></head>"
        "<body><div>Post body</div>"
        "<h2>More from this Page</h2>"
        "<img src='https://scontent.fbcdn.net/related1.jpg'>"
        "<img src='https://scontent.fbcdn.net/related2.jpg'>"
        "</body>"
    )
    truncated = _truncate_at_related_content(html)
    assert "post.jpg" in truncated
    assert "related1.jpg" not in truncated
    assert "related2.jpg" not in truncated


def test_truncate_at_related_content_drops_suggestions():
    """'Suggested for You' boundary cuts the doc."""
    html = (
        "<div>main post <img src='real.jpg'></div>"
        "<div>Suggested for You</div>"
        "<img src='suggested.jpg'>"
    )
    truncated = _truncate_at_related_content(html)
    assert "real.jpg" in truncated
    assert "suggested.jpg" not in truncated


def test_truncate_at_related_content_no_boundary_keeps_html():
    """Without a boundary marker, the full HTML is returned."""
    html = "<div>only the post here, nothing else</div>"
    assert _truncate_at_related_content(html) == html


# ---------------------------------------------------------------------------
# Author extraction
# ---------------------------------------------------------------------------


def test_extract_author_from_jsonld():
    """JSON-LD author.name is preferred when present."""
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "SocialMediaPosting", "author": {"@type": "Person", "name": "Jane Doe"}}
    </script>
    </head></html>
    """
    assert _extract_author_from_html(html) == "Jane Doe"


def test_extract_author_from_mbasic_header():
    """mbasic <h3><strong><a> structure yields the author name."""
    html = (
        '<div id="m_story_permalink_view">'
        '<h3><strong><a href="/zuck">Mark Zuckerberg</a></strong></h3>'
        "<div>post body</div></div>"
    )
    assert _extract_author_from_html(html) == "Mark Zuckerberg"


def test_extract_author_from_og_title():
    """og:title is the last-resort source; trailing ' | Facebook' is stripped."""
    html = '<meta property="og:title" content="Cool Page | Facebook">'
    assert _extract_author_from_html(html) == "Cool Page"


def test_extract_author_jsonld_takes_precedence_over_og_title():
    """JSON-LD wins over og:title when both are present."""
    html = """
    <meta property="og:title" content="Wrong Author | Facebook">
    <script type="application/ld+json">
    {"author": {"name": "Right Author"}}
    </script>
    """
    assert _extract_author_from_html(html) == "Right Author"


def test_extract_author_returns_none_when_absent():
    """No usable author markers → None."""
    html = "<html><body>just text, no author markers</body></html>"
    assert _extract_author_from_html(html) is None


def test_extract_author_skips_invalid_jsonld():
    """Malformed JSON-LD doesn't crash; falls through to og:title."""
    html = """
    <script type="application/ld+json">{this is not valid json</script>
    <meta property="og:title" content="Fallback Author">
    """
    assert _extract_author_from_html(html) == "Fallback Author"


# ---------------------------------------------------------------------------
# mbasic fallback guards (DAN-65: prod-extraction hardening)
# ---------------------------------------------------------------------------


def _make_text_response(text: str, final_url: str, status: int = 200):
    """Mock an aiohttp text response with a configurable final url."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.text = AsyncMock(return_value=text)
    resp.url = final_url
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


@pytest.mark.asyncio
async def test_mbasic_bails_on_login_redirect():
    """mbasic must fail loudly when FB redirects the page to /login.php."""
    login_url = (
        "https://mbasic.facebook.com/login.php?next=https://mbasic.facebook.com/foo/posts/abc"
    )
    mock_session = AsyncMock()
    mock_session.get = MagicMock(
        return_value=_make_text_response("<html>login form</html>", login_url)
    )
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = FacebookScraper()
        with pytest.raises(RuntimeError, match="redirected to login"):
            await scraper._mbasic_fallback("https://www.facebook.com/foo/posts/abc")


@pytest.mark.asyncio
async def test_mbasic_bails_on_checkpoint_redirect():
    """Same guard fires for FB's checkpoint (re-auth challenge) redirect."""
    cp_url = "https://mbasic.facebook.com/checkpoint/?next=foo"
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=_make_text_response("<html></html>", cp_url))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = FacebookScraper()
        with pytest.raises(RuntimeError, match="checkpoint"):
            await scraper._mbasic_fallback("https://www.facebook.com/foo/posts/abc")


@pytest.mark.asyncio
async def test_mbasic_filters_out_static_xx_ui_assets():
    """static.xx.fbcdn.net URLs (FB chrome) are excluded before download attempts."""
    html = (
        '<meta property="og:image" '
        'content="https://scontent.fbcdn.net/v/post-photo.jpg">'
        '<img src="https://static.xx.fbcdn.net/rsrc.php/y8/r/icon.webp">'
        '<img src="https://static.xx.fbcdn.net/rsrc.php/yl/r/sprite.webp">'
    )
    final_url = "https://mbasic.facebook.com/foo/posts/abc"

    page_resp = _make_text_response(html, final_url)
    image_resp = _make_bytes_response(b"x" * 10_000)

    call_count = {"n": 0}

    def get_router(*args, **kwargs):
        call_count["n"] += 1
        # First call is the page fetch, subsequent are image downloads
        return page_resp if call_count["n"] == 1 else image_resp

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=get_router)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = FacebookScraper()
        result = await scraper._mbasic_fallback("https://www.facebook.com/foo/posts/abc")

    # Only the scontent post image survives — static.xx UI assets were skipped
    assert len(result.media_items) == 1
    assert "scontent.fbcdn.net" in result.media_items[0].url
    assert all("static.xx.fbcdn.net" not in item.url for item in result.media_items)
