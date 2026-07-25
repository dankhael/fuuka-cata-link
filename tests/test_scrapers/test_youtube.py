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
async def test_backstop_cap_when_download_metadata_disagrees():
    """The probe's numbers aren't final — when the download reports a duration
    over the cap, that still wins and the video is dropped (DAN-80)."""
    scraper = YouTubeScraper()
    over_cap = _result(title="too long", duration=400.0, data=b"bytes")

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=over_cap)):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/longvideo")


@pytest.mark.asyncio
async def test_oversized_video_skipped_before_download():
    """A video too big to send is dropped by the up-front probe — no transfer,
    and no error message in the chat."""
    scraper = YouTubeScraper()
    download = AsyncMock()
    huge = {"duration": 60, "filesize_approx": 900 * 1024 * 1024}

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value=huge)):
        with patch("src.scrapers.youtube.ytdlp_download", new=download):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/hugevideo")

    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_oversized_download_skips_instead_of_erroring(short_video_info):
    """When the probe can't predict the size, yt-dlp aborts on --max-filesize and
    returns no data. That must stay silent, not surface as an extraction error —
    the empty `data` used to raise RuntimeError and reply to the chat."""
    scraper = YouTubeScraper()
    too_large = _result(title="huge", duration=120.0, data=None, exceeds_size_limit=True)

    with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=too_large)):
        with pytest.raises(SkipExtraction):
            await scraper._primary_extract("https://youtu.be/hugevideo")


@pytest.mark.asyncio
async def test_probe_failure_skips_instead_of_downloading_on_spec():
    """A failed probe means we can't prove the video is sendable. The probe and
    the download share one yt-dlp auth path, so a bot-gated probe predicts a
    bot-gated download: skip quietly rather than burn the transfer and reply to
    the chat with an error about a video we may never have posted anyway."""
    scraper = YouTubeScraper()
    download = AsyncMock()

    with patch(
        "src.scrapers.youtube.ytdlp_info",
        new=AsyncMock(side_effect=RuntimeError("Sign in to confirm you're not a bot")),
    ):
        with patch("src.scrapers.youtube.ytdlp_download", new=download):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/gated")

    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_without_duration_skips():
    """Degraded metadata ("Other metadata may also be missing") can't prove the
    video is under the cap, so it doesn't earn a download either."""
    scraper = YouTubeScraper()
    download = AsyncMock()

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"title": "x"})):
        with patch("src.scrapers.youtube.ytdlp_download", new=download):
            with pytest.raises(SkipExtraction):
                await scraper._primary_extract("https://youtu.be/nometadata")

    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_failure_for_a_sendable_video_still_raises():
    """The flip side of gating: once a video is proven within the caps, a failed
    download is a real error and must still reach the chat."""
    scraper = YouTubeScraper()

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})):
        with patch(
            "src.scrapers.youtube.ytdlp_download",
            new=AsyncMock(side_effect=RuntimeError("yt-dlp download failed: boom")),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await scraper._primary_extract("https://youtu.be/short")


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
