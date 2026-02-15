import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.scrapers.twitter import TwitterScraper
from src.scrapers.base import MediaType
from src.utils.link_detector import Platform


@pytest.mark.asyncio
async def test_twitter_primary_extract():
    """Test vxtwitter API extraction."""
    mock_response_data = {
        "user_name": "testuser",
        "text": "Hello from Twitter!",
        "media_extended": [
            {"type": "image", "url": "https://pbs.twimg.com/media/test.jpg"},
            {"type": "video", "url": "https://video.twimg.com/test.mp4"},
        ],
    }

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=mock_response_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://twitter.com/user/status/123")

    assert result.platform == Platform.TWITTER
    assert result.author == "testuser"
    assert result.caption == "Hello from Twitter!"
    assert len(result.media_items) == 2
    assert result.media_items[0].media_type == MediaType.IMAGE
    assert result.media_items[1].media_type == MediaType.VIDEO


@pytest.mark.asyncio
async def test_twitter_no_media():
    """Test text-only tweet."""
    mock_response_data = {
        "user_name": "testuser",
        "text": "Just text, no media",
        "media_extended": [],
    }

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=mock_response_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/user/status/456")

    assert result.has_media is False
    assert result.caption == "Just text, no media"
