import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from yarl import URL

from src.scrapers.reddit import RedditScraper


@pytest.mark.asyncio
async def test_resolve_shortlink_follows_redirect():
    """Test that /s/ share shortlinks are resolved via redirect."""
    scraper = RedditScraper()

    mock_resp = AsyncMock()
    mock_resp.url = URL("https://www.reddit.com/r/dragonquest/comments/abc123/some_post/")
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.head = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        resolved = await scraper._resolve_shortlink(
            "https://www.reddit.com/r/dragonquest/s/QKeT03pQUT"
        )

    assert resolved == "https://www.reddit.com/r/dragonquest/comments/abc123/some_post/"
    assert "/s/" not in resolved


@pytest.mark.asyncio
async def test_resolve_shortlink_skips_non_shortlinks():
    """Test that regular Reddit URLs are not modified."""
    scraper = RedditScraper()
    url = "https://www.reddit.com/r/python/comments/abc123/some_title/"
    resolved = await scraper._resolve_shortlink(url)
    assert resolved == url


@pytest.mark.asyncio
async def test_resolve_shortlink_handles_failure():
    """Test graceful fallback when redirect resolution fails."""
    scraper = RedditScraper()
    original = "https://www.reddit.com/r/test/s/BADLINK"

    with patch("aiohttp.ClientSession", side_effect=Exception("network error")):
        resolved = await scraper._resolve_shortlink(original)

    # Should return the original URL on failure
    assert resolved == original
