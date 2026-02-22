from unittest.mock import AsyncMock, patch

import pytest

from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.scrapers.facebook import FacebookScraper, _read_cookies_for_domain
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult

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
# gallery-dl fallback (image posts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facebook_image_post_via_gallery_dl():
    """Image post falls through yt-dlp to gallery-dl."""
    gallery_result = ScrapedMedia(
        platform=Platform.FACEBOOK,
        original_url="https://www.facebook.com/photo/123",
        author="photographer",
        caption="Nice photo",
        media_items=[
            MediaItem(
                url="https://www.facebook.com/photo/123",
                media_type=MediaType.IMAGE,
                data=b"image_data",
            ),
        ],
    )

    with (
        patch(
            "src.scrapers.facebook.ytdlp_download",
            new_callable=AsyncMock,
            side_effect=RuntimeError("No video formats found"),
        ),
        patch(
            "src.scrapers.facebook.FacebookScraper._gallery_dl_extract",
            new_callable=AsyncMock,
            return_value=gallery_result,
        ),
    ):
        scraper = FacebookScraper()
        result = await scraper._primary_extract("https://www.facebook.com/photo/123")

    assert result.author == "photographer"
    assert result.caption == "Nice photo"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.IMAGE


# ---------------------------------------------------------------------------
# Full fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facebook_fallback_to_mbasic():
    """When yt-dlp, gallery-dl, and fdown fail, falls through to mbasic."""
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
        patch(
            "src.scrapers.facebook.ytdlp_download",
            new_callable=AsyncMock,
            side_effect=RuntimeError("yt-dlp failed"),
        ),
        patch(
            "src.scrapers.facebook.FacebookScraper._gallery_dl_extract",
            new_callable=AsyncMock,
            side_effect=RuntimeError("gallery-dl failed"),
        ),
        patch(
            "src.scrapers.facebook.FacebookScraper._fdown_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fdown failed"),
        ),
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
