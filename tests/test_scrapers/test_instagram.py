from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.scrapers.instagram import InstagramScraper
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult


def _make_html_response(html: str):
    """Create a mock aiohttp response that returns HTML text."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.text = AsyncMock(return_value=html)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_bytes_response(data: bytes):
    """Create a mock aiohttp response that returns bytes."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# yt-dlp success path (reels/videos)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instagram_reel_via_ytdlp():
    """Reel extraction works via yt-dlp (existing behavior)."""
    mock_result = YtdlpResult(
        title="Cool reel",
        description="Check this out",
        uploader="creator",
        data=b"video_bytes",
        is_video=True,
    )

    with patch(
        "src.scrapers.instagram.ytdlp_download",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        scraper = InstagramScraper()
        result = await scraper._primary_extract("https://www.instagram.com/reel/ABC123/")

    assert result.platform == Platform.INSTAGRAM
    assert result.author == "creator"
    assert result.caption == "Check this out"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    assert result.media_items[0].data == b"video_bytes"


# ---------------------------------------------------------------------------
# gallery-dl fallback (image posts)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instagram_image_post_via_gallery_dl():
    """Image post falls through yt-dlp to gallery-dl."""
    with (
        patch(
            "src.scrapers.instagram.ytdlp_download",
            new_callable=AsyncMock,
            side_effect=RuntimeError("No video formats found"),
        ),
        patch(
            "src.scrapers.instagram.InstagramScraper._gallery_dl_extract",
            new_callable=AsyncMock,
        ) as mock_gdl,
    ):
        mock_gdl.return_value = ScrapedMedia(
            platform=Platform.INSTAGRAM,
            original_url="https://www.instagram.com/p/XYZ789/",
            author="photographer",
            caption="Beautiful sunset",
            media_items=[
                MediaItem(
                    url="https://www.instagram.com/p/XYZ789/",
                    media_type=MediaType.IMAGE,
                    data=b"image_data_1",
                ),
                MediaItem(
                    url="https://www.instagram.com/p/XYZ789/",
                    media_type=MediaType.IMAGE,
                    data=b"image_data_2",
                ),
            ],
        )

        scraper = InstagramScraper()
        result = await scraper._primary_extract("https://www.instagram.com/p/XYZ789/")

    assert result.author == "photographer"
    assert result.caption == "Beautiful sunset"
    assert len(result.media_items) == 2
    assert all(item.media_type == MediaType.IMAGE for item in result.media_items)


# ---------------------------------------------------------------------------
# Embed page fallback
# ---------------------------------------------------------------------------


EMBED_HTML = (
    "<html><head>"
    '<meta property="og:image" content='
    '"https://scontent-lax3-1.cdninstagram.com/v/photo1.jpg?x=1" />'
    '<meta property="og:description" content="A beautiful day" />'
    "</head><body></body></html>"
)


@pytest.mark.asyncio
async def test_instagram_embed_fallback():
    """Embed fallback extracts og:image from embed page."""
    embed_resp = _make_html_response(EMBED_HTML)
    image_data = b"x" * 10_000  # > 5KB threshold
    image_resp = _make_bytes_response(image_data)

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return embed_resp  # First call: fetch embed HTML
        return image_resp  # Subsequent calls: download images

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=mock_get)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = InstagramScraper()
        result = await scraper._embed_fallback("https://www.instagram.com/p/TEST123/")

    assert result.platform == Platform.INSTAGRAM
    assert result.caption == "A beautiful day"
    assert len(result.media_items) >= 1
    assert result.media_items[0].media_type == MediaType.IMAGE
    assert result.media_items[0].data == image_data


@pytest.mark.asyncio
async def test_instagram_embed_fallback_no_shortcode():
    """Embed fallback raises if shortcode can't be extracted."""
    scraper = InstagramScraper()
    with pytest.raises(RuntimeError, match="shortcode"):
        await scraper._embed_fallback("https://www.instagram.com/stories/user/")


# ---------------------------------------------------------------------------
# Full fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instagram_all_methods_fail():
    """When all methods fail, the last error propagates."""
    with (
        patch(
            "src.scrapers.instagram.ytdlp_download",
            new_callable=AsyncMock,
            side_effect=RuntimeError("yt-dlp failed"),
        ),
        patch(
            "src.scrapers.instagram.InstagramScraper._gallery_dl_extract",
            new_callable=AsyncMock,
            side_effect=RuntimeError("gallery-dl failed"),
        ),
        patch(
            "src.scrapers.instagram.InstagramScraper._embed_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embed failed"),
        ),
    ):
        scraper = InstagramScraper()
        with pytest.raises(RuntimeError, match="embed failed"):
            await scraper._primary_extract("https://www.instagram.com/p/FAIL/")
