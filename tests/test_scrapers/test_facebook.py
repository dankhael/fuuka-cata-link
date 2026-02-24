from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.scrapers.facebook import (
    FacebookScraper,
    _clean_facebook_url,
    _read_cookies_for_domain,
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
        result = await scraper._fbscraper_fallback(
            "https://www.facebook.com/photo/123"
        )

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
        result = await scraper._fbscraper_fallback(
            "https://www.facebook.com/watch?v=456"
        )

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
    cookies_file.write_text(
        ".google.com\tTRUE\t/\tTRUE\t0\tNID\t999\n"
    )

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
