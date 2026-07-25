from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import structlog

from src.scrapers.base import SkipExtraction
from src.scrapers.youtube import YouTubeScraper
from src.utils.ytdlp import YtdlpResult

_PROXY = "socks5h://botproxy:hunter2@10.0.0.1:1080"


def _result(**overrides) -> YtdlpResult:
    """A downloaded-video result with only the fields these tests care about."""
    return YtdlpResult(uploader="someone", **overrides)


@pytest.fixture
def proxy_settings(request):
    """Patch the scraper's settings; parametrize indirectly to change the proxy."""
    proxy = getattr(request, "param", _PROXY)
    with patch("src.scrapers.youtube.settings") as cfg:
        cfg.youtube_proxy = proxy
        cfg.cookies_file = None
        yield cfg


@pytest.fixture
def short_video_info():
    """Keep the up-front duration probe (DAN-80) out of the way of proxy tests."""
    with patch(
        "src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})
    ) as info:
        yield info


@pytest.fixture
def reachable_proxy():
    with patch("src.scrapers.youtube.is_proxy_reachable", new=AsyncMock(return_value=True)) as p:
        yield p


@pytest.fixture
def dead_proxy():
    with patch("src.scrapers.youtube.is_proxy_reachable", new=AsyncMock(return_value=False)) as p:
        yield p


@pytest.mark.asyncio
async def test_video_over_cap_skipped_before_download():
    """A long video is dropped by the up-front metadata probe (DAN-80) — the
    download must not run at all, so we never waste a proxy-slow transfer."""
    scraper = YouTubeScraper()
    download = AsyncMock()

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 400})):
        with patch("src.scrapers.youtube.ytdlp_download", new=download):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/longvideo")

    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_within_cap_returns_media(short_video_info):
    scraper = YouTubeScraper()
    within_cap = _result(title="ok", duration=120.0, data=b"video-bytes")

    with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=within_cap)):
        result = await scraper._primary_extract("https://youtu.be/short")

    assert result.has_media
    assert result.media_items[0].data == b"video-bytes"
    assert result.caption == "ok"


@pytest.mark.asyncio
async def test_backstop_cap_when_probe_lacks_duration():
    """When the probe returns no duration, the download's own metadata still
    enforces the cap — the long video must not slip through (DAN-80)."""
    scraper = YouTubeScraper()
    over_cap = _result(title="too long", duration=400.0, data=b"bytes")

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={})):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=over_cap)):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/longvideo")


@pytest.mark.asyncio
async def test_probe_failure_does_not_block_valid_video():
    """A flaky metadata probe (proxy/network) must not lose a short, valid
    video — the failure is swallowed and the download still runs (DAN-80)."""
    scraper = YouTubeScraper()
    within_cap = _result(title="ok", duration=120.0, data=b"video-bytes")

    with patch(
        "src.scrapers.youtube.ytdlp_info",
        new=AsyncMock(side_effect=RuntimeError("yt-dlp info failed: timed out")),
    ):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=within_cap)):
            result = await scraper._primary_extract("https://youtu.be/short")

    assert result.has_media


def test_ytdlp_extract_is_not_aliased_to_primary():
    """The base chain calls _primary_extract *and* _ytdlp_extract, so aliasing
    them made every failing YouTube link probe and download twice over."""
    assert "_ytdlp_extract" not in vars(YouTubeScraper)


@pytest.mark.asyncio
async def test_proxy_used_when_reachable(proxy_settings, short_video_info, reachable_proxy):
    """A reachable proxy is passed straight through, with no direct retry."""
    ok = _result(duration=10.0, data=b"bytes")

    with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=ok)) as download:
        await YouTubeScraper()._primary_extract("https://youtu.be/x")

    download.assert_awaited_once()
    assert download.await_args.kwargs["proxy"] == _PROXY
    assert short_video_info.await_args.kwargs["proxy"] == _PROXY


@pytest.mark.asyncio
async def test_unreachable_proxy_is_skipped_before_downloading(
    proxy_settings, short_video_info, dead_proxy
):
    """A dead proxy must not even be handed to yt-dlp: it would burn its retries
    and wall-clock ceiling, then report an OS-localized "connection refused"
    that no error pattern can attribute back to the proxy."""
    ok = _result(duration=10.0, data=b"bytes")

    with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=ok)) as download:
        result = await YouTubeScraper()._primary_extract("https://youtu.be/x")

    assert result.has_media
    download.assert_awaited_once()
    assert download.await_args.kwargs.get("proxy") is None
    assert short_video_info.await_args.kwargs.get("proxy") is None


@pytest.mark.asyncio
async def test_failure_through_reachable_proxy_retries_direct(
    proxy_settings, short_video_info, reachable_proxy
):
    """Reachable but broken (e.g. a SOCKS auth reject) still falls back to direct."""
    ok = _result(duration=10.0, data=b"bytes")
    attempts = AsyncMock(side_effect=[RuntimeError("yt-dlp download failed: nope"), ok])

    with patch("src.scrapers.youtube.ytdlp_download", new=attempts):
        result = await YouTubeScraper()._primary_extract("https://youtu.be/x")

    assert result.has_media
    assert attempts.await_count == 2
    assert attempts.await_args_list[0].kwargs.get("proxy") == _PROXY
    assert attempts.await_args_list[1].kwargs.get("proxy") is None


@pytest.mark.asyncio
async def test_proxy_credentials_never_reach_the_logs(proxy_settings, short_video_info, dead_proxy):
    """These warnings are persisted to logs/errors.log — the SOCKS password
    must not travel with them."""
    ok = _result(duration=10.0, data=b"bytes")

    with structlog.testing.capture_logs() as events:
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=ok)):
            await YouTubeScraper()._primary_extract("https://youtu.be/x")

    warning = next(e for e in events if e["event"] == "youtube_proxy_unreachable")
    assert warning["proxy"] == "socks5h://botproxy:***@10.0.0.1:1080"
    assert "hunter2" not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_settings", [None], indirect=True)
async def test_no_proxy_configured_downloads_directly(proxy_settings, short_video_info):
    ok = _result(duration=10.0, data=b"bytes")
    probe = AsyncMock(return_value=True)

    with patch("src.scrapers.youtube.is_proxy_reachable", new=probe):
        with patch(
            "src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=ok)
        ) as download:
            await YouTubeScraper()._primary_extract("https://youtu.be/x")

    probe.assert_not_awaited()
    download.assert_awaited_once()
    assert download.await_args.kwargs.get("proxy") is None
