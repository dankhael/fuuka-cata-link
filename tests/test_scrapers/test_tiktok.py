import pytest
from unittest.mock import AsyncMock, patch

from src.scrapers.tiktok import TikTokScraper
from src.scrapers.base import MediaType
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult


@pytest.mark.asyncio
async def test_tiktok_primary_extract_with_data():
    """Test that TikTok scraper uses yt-dlp direct download and pre-populates data."""
    mock_result = YtdlpResult(
        title="Cool TikTok",
        description="Check this out",
        uploader="creator123",
        data=b"fake_video_bytes",
        is_video=True,
    )

    with patch("src.scrapers.tiktok.ytdlp_download", new_callable=AsyncMock, return_value=mock_result):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://www.tiktok.com/@user/video/123")

    assert result.platform == Platform.TIKTOK
    assert result.author == "creator123"
    assert result.caption == "Check this out"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    # Data should be pre-populated (no separate download needed)
    assert result.media_items[0].data == b"fake_video_bytes"


@pytest.mark.asyncio
async def test_tiktok_extract_no_data():
    """Test that TikTok scraper handles yt-dlp returning no data."""
    mock_result = YtdlpResult(
        title="TikTok",
        uploader="user",
        data=None,
        is_video=True,
    )

    with patch("src.scrapers.tiktok.ytdlp_download", new_callable=AsyncMock, return_value=mock_result):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://www.tiktok.com/@user/video/123")

    assert result.has_media is False
