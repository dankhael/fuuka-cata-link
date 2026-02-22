from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers.base import MediaType
from src.scrapers.tiktok import TikTokScraper
from src.utils.link_detector import Platform
from src.utils.ytdlp import YtdlpResult


def _tikwm_response(post_data: dict) -> dict:
    """Wrap post data in tikwm API envelope."""
    return {"code": 0, "msg": "success", "data": post_data}


def _make_json_response(data: dict):
    """Create a mock aiohttp response that returns JSON."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value=data)
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


def _make_session_with_responses(responses: list):
    """Create a mock aiohttp session that returns sequential responses."""
    call_count = 0

    def side_effect_get(_url, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    session = AsyncMock()
    session.get = MagicMock(side_effect=side_effect_get)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# Photo carousel extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiktok_photo_carousel():
    """Photo post with multiple images returns IMAGE media items."""
    api_data = _tikwm_response({
        "title": "Cool photo set",
        "author": {"unique_id": "photographer"},
        "images": [
            "https://cdn.tiktok.com/img1.jpg",
            "https://cdn.tiktok.com/img2.jpg",
            "https://cdn.tiktok.com/img3.jpg",
        ],
    })

    responses = [
        _make_json_response(api_data),
        _make_bytes_response(b"image_bytes_1"),
        _make_bytes_response(b"image_bytes_2"),
        _make_bytes_response(b"image_bytes_3"),
    ]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://vt.tiktok.com/ZSm9fd6hk/")

    assert result.platform == Platform.TIKTOK
    assert result.author == "photographer"
    assert result.caption == "Cool photo set"
    assert len(result.media_items) == 3
    for item in result.media_items:
        assert item.media_type == MediaType.IMAGE
        assert item.data is not None


@pytest.mark.asyncio
async def test_tiktok_single_photo():
    """Photo post with a single image works correctly."""
    api_data = _tikwm_response({
        "title": "One pic",
        "author": {"unique_id": "user1"},
        "images": ["https://cdn.tiktok.com/single.jpg"],
    })

    responses = [
        _make_json_response(api_data),
        _make_bytes_response(b"single_image_data"),
    ]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://www.tiktok.com/@user/photo/123")

    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.IMAGE
    assert result.media_items[0].data == b"single_image_data"


# ---------------------------------------------------------------------------
# Video extraction via tikwm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiktok_video_via_tikwm():
    """Video post downloads from hdplay URL."""
    api_data = _tikwm_response({
        "title": "Funny video",
        "author": {"unique_id": "videomaker"},
        "hdplay": "https://cdn.tiktok.com/hd_video.mp4",
        "play": "https://cdn.tiktok.com/sd_video.mp4",
    })

    responses = [
        _make_json_response(api_data),
        _make_bytes_response(b"hd_video_bytes"),
    ]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://www.tiktok.com/@user/video/456")

    assert result.author == "videomaker"
    assert result.caption == "Funny video"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    assert result.media_items[0].data == b"hd_video_bytes"
    # Verify hdplay was used (first non-API call)
    assert mock_session.get.call_count == 2


@pytest.mark.asyncio
async def test_tiktok_video_sd_fallback():
    """When hdplay is empty, falls back to play URL."""
    api_data = _tikwm_response({
        "title": "SD video",
        "author": {"unique_id": "user"},
        "hdplay": "",
        "play": "https://cdn.tiktok.com/sd_video.mp4",
    })

    responses = [
        _make_json_response(api_data),
        _make_bytes_response(b"sd_video_bytes"),
    ]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://www.tiktok.com/@user/video/789")

    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    assert result.media_items[0].data == b"sd_video_bytes"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiktok_tikwm_api_error():
    """tikwm API returning error code raises RuntimeError."""
    api_data = {"code": -1, "msg": "Video not found"}

    responses = [_make_json_response(api_data)]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        with pytest.raises(RuntimeError, match="tikwm API error"):
            await scraper._primary_extract("https://www.tiktok.com/@user/video/999")


@pytest.mark.asyncio
async def test_tiktok_photo_partial_download_failure():
    """If some images fail to download, remaining images are still returned."""
    api_data = _tikwm_response({
        "title": "Partial",
        "author": {"unique_id": "user"},
        "images": [
            "https://cdn.tiktok.com/ok.jpg",
            "https://cdn.tiktok.com/broken.jpg",
            "https://cdn.tiktok.com/also_ok.jpg",
        ],
    })

    # Second image download fails
    error_resp = AsyncMock()
    error_resp.raise_for_status = MagicMock(side_effect=Exception("Connection error"))
    error_resp.__aenter__ = AsyncMock(return_value=error_resp)
    error_resp.__aexit__ = AsyncMock(return_value=False)

    responses = [
        _make_json_response(api_data),
        _make_bytes_response(b"image_ok_1"),
        error_resp,
        _make_bytes_response(b"image_ok_2"),
    ]
    mock_session = _make_session_with_responses(responses)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        scraper = TikTokScraper()
        result = await scraper._primary_extract("https://vt.tiktok.com/test/")

    assert len(result.media_items) == 2
    assert result.media_items[0].data == b"image_ok_1"
    assert result.media_items[1].data == b"image_ok_2"


# ---------------------------------------------------------------------------
# yt-dlp fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tiktok_ytdlp_fallback():
    """yt-dlp fallback works for video posts when tikwm fails."""
    mock_result = YtdlpResult(
        title="Cool TikTok",
        description="Check this out",
        uploader="creator123",
        data=b"fake_video_bytes",
        is_video=True,
    )

    with patch(
        "src.scrapers.tiktok.ytdlp_download",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        scraper = TikTokScraper()
        result = await scraper._ytdlp_extract("https://www.tiktok.com/@user/video/123")

    assert result.platform == Platform.TIKTOK
    assert result.author == "creator123"
    assert result.caption == "Check this out"
    assert len(result.media_items) == 1
    assert result.media_items[0].media_type == MediaType.VIDEO
    assert result.media_items[0].data == b"fake_video_bytes"


@pytest.mark.asyncio
async def test_tiktok_ytdlp_no_data_raises():
    """yt-dlp returning no data raises RuntimeError."""
    mock_result = YtdlpResult(title="TikTok", uploader="user", data=None, is_video=True)

    with patch(
        "src.scrapers.tiktok.ytdlp_download",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        scraper = TikTokScraper()
        with pytest.raises(RuntimeError, match="no data"):
            await scraper._ytdlp_extract("https://www.tiktok.com/@user/video/123")
