from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import MediaType
from src.scrapers.twitter import TwitterScraper
from src.utils.link_detector import Platform


def _make_mock_response(data):
    """Create an aiohttp-compatible async context-manager mock response."""
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    return mock_resp


def _make_mock_session(response):
    """Create an aiohttp.ClientSession mock that returns *response* on get()."""
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _fx_tweet(tweet_data: dict) -> dict:
    """Wrap tweet data in fxtwitter API envelope."""
    return {"code": 200, "message": "OK", "tweet": tweet_data}


# ---------------------------------------------------------------------------
# Basic extraction (fxtwitter format)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_twitter_primary_extract():
    """Test fxtwitter API extraction with image and video."""
    api_data = _fx_tweet({
        "text": "Hello from Twitter!",
        "author": {"screen_name": "testuser", "name": "Test User"},
        "media": {
            "all": [
                {"type": "image", "url": "https://pbs.twimg.com/media/test.jpg"},
                {"type": "video", "url": "https://video.twimg.com/test.mp4"},
            ],
        },
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://twitter.com/user/status/123")

    assert result.platform == Platform.TWITTER
    assert result.author == "testuser"
    assert result.caption == "Hello from Twitter!"
    assert len(result.media_items) == 2
    assert result.media_items[0].media_type == MediaType.IMAGE
    assert result.media_items[1].media_type == MediaType.VIDEO
    assert result.referenced_post is None
    assert result.reference_type is None


@pytest.mark.asyncio
async def test_twitter_no_media():
    """Test text-only tweet with fxtwitter format."""
    api_data = _fx_tweet({
        "text": "Just text, no media",
        "author": {"screen_name": "testuser"},
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/user/status/456")

    assert result.has_media is False
    assert result.caption == "Just text, no media"
    assert result.referenced_post is None


@pytest.mark.asyncio
async def test_twitter_media_photos_videos_fallback():
    """When media.all is absent, fall back to photos + videos arrays."""
    api_data = _fx_tweet({
        "text": "Mixed media",
        "author": {"screen_name": "user"},
        "media": {
            "photos": [{"url": "https://img.com/a.jpg"}],
            "videos": [{"url": "https://vid.com/b.mp4"}],
        },
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/u/status/1")

    assert len(result.media_items) == 2
    assert result.media_items[0].media_type == MediaType.IMAGE
    assert result.media_items[1].media_type == MediaType.VIDEO


# ---------------------------------------------------------------------------
# Quote tweet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_twitter_quote_tweet():
    """Quote tweet populates referenced_post with the quoted tweet data."""
    api_data = _fx_tweet({
        "text": "Look at this!",
        "author": {"screen_name": "quoter"},
        "quote": {
            "url": "https://x.com/original/status/456",
            "text": "Original content",
            "author": {"screen_name": "original_user"},
            "media": {
                "photos": [{"url": "https://pbs.twimg.com/orig.jpg"}],
            },
        },
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/quoter/status/789")

    assert result.reference_type == "quote"
    assert result.referenced_post is not None
    assert result.referenced_post.author == "original_user"
    assert result.referenced_post.caption == "Original content"
    assert result.referenced_post.original_url == "https://x.com/original/status/456"
    assert len(result.referenced_post.media_items) == 1
    assert result.referenced_post.media_items[0].media_type == MediaType.IMAGE


@pytest.mark.asyncio
async def test_twitter_quote_tweet_text_only():
    """Quote tweet where quoted tweet has no media."""
    api_data = _fx_tweet({
        "text": "Commenting on this",
        "author": {"screen_name": "quoter"},
        "quote": {
            "url": "https://x.com/op/status/111",
            "text": "Original hot take",
            "author": {"screen_name": "op"},
        },
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/quoter/status/222")

    assert result.reference_type == "quote"
    assert result.referenced_post is not None
    assert result.referenced_post.has_media is False
    assert result.referenced_post.caption == "Original hot take"


# ---------------------------------------------------------------------------
# Reply tweet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_twitter_reply():
    """Reply tweet fetches parent via second API call."""
    reply_api = _fx_tweet({
        "text": "I agree!",
        "author": {"screen_name": "replier"},
        "replying_to": "parent_user",
        "replying_to_status": "456",
    })
    parent_api = _fx_tweet({
        "url": "https://x.com/parent_user/status/456",
        "text": "Original take",
        "author": {"screen_name": "parent_user"},
        "media": {
            "photos": [{"url": "https://pbs.twimg.com/parent.jpg"}],
        },
    })

    reply_resp = _make_mock_response(reply_api)
    parent_resp = _make_mock_response(parent_api)

    # Return different responses for sequential get() calls
    call_count = 0
    responses = [reply_resp, parent_resp]

    def side_effect_get(_url, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=side_effect_get)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/replier/status/789")

    assert result.caption == "I agree!"
    assert result.reference_type == "reply"
    assert result.referenced_post is not None
    assert result.referenced_post.author == "parent_user"
    assert result.referenced_post.caption == "Original take"
    assert result.referenced_post.original_url == "https://x.com/parent_user/status/456"
    assert len(result.referenced_post.media_items) == 1

    # Verify second API call was made
    assert mock_session.get.call_count == 2


@pytest.mark.asyncio
async def test_twitter_reply_parent_unavailable():
    """When parent tweet fetch fails, reply is returned without referenced_post."""
    reply_api = _fx_tweet({
        "text": "Replying to deleted tweet",
        "author": {"screen_name": "replier"},
        "replying_to": "deleted_user",
        "replying_to_status": "999",
    })

    reply_resp = _make_mock_response(reply_api)

    # Second call raises an error (parent unavailable)
    error_resp = AsyncMock()
    error_resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))
    error_resp.__aenter__ = AsyncMock(return_value=error_resp)
    error_resp.__aexit__ = AsyncMock(return_value=False)

    call_count = 0
    responses = [reply_resp, error_resp]

    def side_effect_get(_url, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    mock_session = AsyncMock()
    mock_session.get = MagicMock(side_effect=side_effect_get)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/replier/status/789")

    # Main tweet still extracted successfully
    assert result.caption == "Replying to deleted tweet"
    assert result.author == "replier"
    # But no referenced_post due to failed parent fetch
    assert result.referenced_post is None
    assert result.reference_type is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_twitter_quote_takes_priority_over_reply():
    """When both quote and replying_to are present, quote is preferred."""
    api_data = _fx_tweet({
        "text": "Quoting while replying",
        "author": {"screen_name": "user"},
        "quote": {
            "url": "https://x.com/quoted/status/100",
            "text": "Quoted text",
            "author": {"screen_name": "quoted_user"},
        },
        "replying_to": "other",
        "replying_to_status": "200",
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/user/status/300")

    # Quote takes priority
    assert result.reference_type == "quote"
    assert result.referenced_post.author == "quoted_user"
    # Only one API call (no second call for reply parent)
    assert mock_session.get.call_count == 1


@pytest.mark.asyncio
async def test_twitter_gif_parsed_as_video():
    """GIF media type in fxtwitter is treated as video."""
    api_data = _fx_tweet({
        "text": "Check this gif",
        "author": {"screen_name": "user"},
        "media": {
            "all": [{"type": "gif", "url": "https://video.twimg.com/gif.mp4"}],
        },
    })

    mock_resp = _make_mock_response(api_data)
    mock_session = _make_mock_session(mock_resp)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TwitterScraper()
        result = await scraper._primary_extract("https://x.com/user/status/1")

    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
