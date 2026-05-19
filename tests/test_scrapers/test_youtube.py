from __future__ import annotations

from unittest.mock import patch

import pytest

from src.scrapers.base import SkipExtraction
from src.scrapers.youtube import YouTubeScraper
from src.utils.ytdlp import YtdlpResult


@pytest.mark.asyncio
async def test_video_over_duration_cap_raises_skip_extraction():
    """Videos longer than the cap must raise ``SkipExtraction`` so the
    handler stays silent — DAN-71 removed the "Video too long" reply."""
    scraper = YouTubeScraper()
    over_cap = YtdlpResult(title="too long", uploader="someone", duration=400.0)

    with patch(
        "src.scrapers.youtube.ytdlp_download",
        return_value=over_cap,
    ):
        with pytest.raises(SkipExtraction):
            await scraper._ytdlp_extract("https://youtu.be/longvideo")


@pytest.mark.asyncio
async def test_video_within_cap_returns_media():
    scraper = YouTubeScraper()
    within_cap = YtdlpResult(
        title="ok",
        uploader="someone",
        duration=120.0,
        data=b"video-bytes",
    )

    with patch(
        "src.scrapers.youtube.ytdlp_download",
        return_value=within_cap,
    ):
        result = await scraper._ytdlp_extract("https://youtu.be/short")

    assert result.has_media
    assert result.media_items[0].data == b"video-bytes"
    assert result.caption == "ok"
