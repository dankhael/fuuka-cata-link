from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.scrapers.base import SkipExtraction
from src.scrapers.youtube import YouTubeScraper
from src.utils.ytdlp import YtdlpResult, is_proxy_connection_error


@pytest.mark.asyncio
async def test_video_over_cap_skipped_before_download():
    """A long video is dropped by the up-front metadata probe (DAN-80) — the
    download must not run at all, so we never waste a proxy-slow transfer."""
    scraper = YouTubeScraper()
    download = AsyncMock()

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 400})):
        with patch("src.scrapers.youtube.ytdlp_download", new=download):
            with pytest.raises(SkipExtraction):
                await scraper._ytdlp_extract("https://youtu.be/longvideo")

    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_within_cap_returns_media():
    scraper = YouTubeScraper()
    within_cap = YtdlpResult(title="ok", uploader="someone", duration=120.0, data=b"video-bytes")

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 120})):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=within_cap)):
            result = await scraper._ytdlp_extract("https://youtu.be/short")

    assert result.has_media
    assert result.media_items[0].data == b"video-bytes"
    assert result.caption == "ok"


@pytest.mark.asyncio
async def test_backstop_cap_when_probe_lacks_duration():
    """When the probe returns no duration, the download's own metadata still
    enforces the cap — the long video must not slip through (DAN-80)."""
    scraper = YouTubeScraper()
    over_cap = YtdlpResult(title="too long", duration=400.0, data=b"bytes")

    with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={})):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=over_cap)):
            with pytest.raises(SkipExtraction):
                await scraper._ytdlp_extract("https://youtu.be/longvideo")


@pytest.mark.asyncio
async def test_probe_failure_does_not_block_valid_video():
    """A flaky metadata probe (proxy/network) must not lose a short, valid
    video — the failure is swallowed and the download still runs (DAN-80)."""
    scraper = YouTubeScraper()
    within_cap = YtdlpResult(title="ok", duration=120.0, data=b"video-bytes")

    with patch(
        "src.scrapers.youtube.ytdlp_info",
        new=AsyncMock(side_effect=RuntimeError("yt-dlp info failed: timed out")),
    ):
        with patch("src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=within_cap)):
            result = await scraper._ytdlp_extract("https://youtu.be/short")

    assert result.has_media


@pytest.mark.asyncio
async def test_proxy_used_when_configured():
    """When youtube_proxy is set, the proxy is passed straight through."""
    scraper = YouTubeScraper()
    ok = YtdlpResult(title="ok", duration=10.0, data=b"bytes")

    with patch("src.scrapers.youtube.settings") as cfg:
        cfg.youtube_proxy = "socks5://10.0.0.1:1080"
        cfg.cookies_file = None
        with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})):
            with patch(
                "src.scrapers.youtube.ytdlp_download", new=AsyncMock(return_value=ok)
            ) as download:
                await scraper._ytdlp_extract("https://youtu.be/x")

    download.assert_awaited_once()
    assert download.await_args.kwargs["proxy"] == "socks5://10.0.0.1:1080"


@pytest.mark.asyncio
async def test_falls_back_to_direct_when_proxy_unreachable():
    """A dead proxy must not break YouTube — retry directly (no proxy kwarg)."""
    scraper = YouTubeScraper()
    ok = YtdlpResult(title="ok", duration=10.0, data=b"bytes")

    with patch("src.scrapers.youtube.settings") as cfg:
        cfg.youtube_proxy = "socks5://10.0.0.1:1080"
        cfg.cookies_file = None
        attempts = AsyncMock(
            side_effect=[RuntimeError("yt-dlp download failed: Unable to connect to proxy"), ok]
        )
        with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})):
            with patch("src.scrapers.youtube.ytdlp_download", new=attempts):
                result = await scraper._ytdlp_extract("https://youtu.be/x")

    assert result.has_media
    assert attempts.await_count == 2
    assert attempts.await_args_list[0].kwargs.get("proxy") == "socks5://10.0.0.1:1080"
    assert attempts.await_args_list[1].kwargs.get("proxy") is None


@pytest.mark.asyncio
async def test_real_failure_through_proxy_is_not_retried():
    """A genuine bot-gate (not a proxy outage) propagates without a direct retry."""
    scraper = YouTubeScraper()

    with patch("src.scrapers.youtube.settings") as cfg:
        cfg.youtube_proxy = "socks5://10.0.0.1:1080"
        cfg.cookies_file = None
        attempts = AsyncMock(
            side_effect=RuntimeError("yt-dlp download failed: Sign in to confirm you're not a bot")
        )
        with patch("src.scrapers.youtube.ytdlp_info", new=AsyncMock(return_value={"duration": 10})):
            with patch("src.scrapers.youtube.ytdlp_download", new=attempts):
                with pytest.raises(RuntimeError):
                    await scraper._ytdlp_extract("https://youtu.be/x")

    assert attempts.await_count == 1


def test_is_proxy_connection_error_discriminates():
    assert is_proxy_connection_error("yt-dlp download failed: Unable to connect to proxy")
    assert is_proxy_connection_error("Error connecting to SOCKS5 proxy")
    assert not is_proxy_connection_error("ERROR: [youtube] Sign in to confirm you're not a bot")
