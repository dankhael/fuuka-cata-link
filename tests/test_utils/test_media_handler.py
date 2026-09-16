from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.scrapers.base import MediaItem, MediaType
from src.utils import media_handler
from src.utils.media_handler import (
    _shrink_video,
    compress_video,
    download_media,
    ensure_within_limit,
    scale_filter,
    target_video_bitrate,
)

MB = 1024 * 1024


def _video(size_mb: float) -> MediaItem:
    item = MediaItem(url="https://example.com/v.mp4", media_type=MediaType.VIDEO)
    item.data = b"v" * int(size_mb * MB)
    return item


class FakeResponse:
    """A minimal aiohttp response: optional Content-Length plus a body."""

    def __init__(self, body: bytes, content_length: int | None) -> None:
        self._body = body
        self.content_length = content_length
        self.read_calls = 0

    def raise_for_status(self) -> None:
        pass

    async def read(self) -> bytes:
        self.read_calls += 1
        return self._body

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc) -> None:
        pass


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def get(self, url: str, **kwargs) -> FakeResponse:
        return self._response


@pytest.fixture
def caps():
    with patch.object(media_handler, "settings") as cfg:
        cfg.max_download_size_mb = 100
        cfg.concurrent_downloads = 3
        cfg.download_timeout_seconds = 5
        cfg.min_video_bitrate_kbps = 500
        cfg.video_encode_preset = "veryfast"
        yield cfg


# ---------------------------------------------------------------------------
# download_media: the ceiling is the download cap, not the Telegram send cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_keeps_videos_over_send_cap_but_under_download_cap(caps):
    """Regression: 60–80MB Twitter videos were dropped as media_too_large even
    though the compression step would have shrunk them to ~10MB."""
    item = MediaItem(url="https://example.com/v.mp4", media_type=MediaType.VIDEO)
    response = FakeResponse(b"v" * 80 * MB, content_length=80 * MB)

    result = await download_media([item], session=FakeSession(response))

    assert result == [item]
    assert item.data is not None


@pytest.mark.asyncio
async def test_download_skips_announced_oversize_before_reading_body(caps):
    item = MediaItem(url="https://example.com/v.mp4", media_type=MediaType.VIDEO)
    response = FakeResponse(b"", content_length=150 * MB)

    result = await download_media([item], session=FakeSession(response))

    assert result == []
    assert response.read_calls == 0


@pytest.mark.asyncio
async def test_download_skips_oversize_body_without_content_length(caps):
    item = MediaItem(url="https://example.com/v.mp4", media_type=MediaType.VIDEO)
    response = FakeResponse(b"v" * 150 * MB, content_length=None)

    result = await download_media([item], session=FakeSession(response))

    assert result == []


# ---------------------------------------------------------------------------
# compress_video: quality floor
# ---------------------------------------------------------------------------


def test_target_video_bitrate_leaves_audio_headroom():
    # 10MB over 60s is ~1.4Mbps total; minus 128k audio, times 0.9 safety.
    assert target_video_bitrate(10 * MB, 60) == 1_143_091


@pytest.mark.asyncio
async def test_compress_video_refuses_target_below_floor():
    """A 10-minute clip can't fit 10MB without dropping under the floor, so the
    encoder must not even start ffmpeg."""
    ffmpeg = AsyncMock()
    with (
        patch.object(media_handler, "_get_video_duration", new=AsyncMock(return_value=600.0)),
        patch.object(media_handler.asyncio, "create_subprocess_exec", new=ffmpeg),
    ):
        result = await compress_video(b"x" * MB, 10 * MB, min_video_bps=500_000)

    assert result is None
    ffmpeg.assert_not_awaited()


def test_scale_filter_never_upscales_and_keeps_height_even():
    assert scale_filter(720) == "scale=-2:'2*trunc(min(720,ih)/2)'"


class FakeFfmpeg:
    """Records the ffmpeg argv and writes a small output file where asked."""

    def __init__(self) -> None:
        self.argv: list[str] = []

    async def __call__(self, *argv: str, **kwargs) -> AsyncMock:
        self.argv = list(argv)
        Path(argv[-1]).write_bytes(b"o" * 1024)
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc


@pytest.mark.asyncio
async def test_compress_video_uses_configured_preset_and_no_upscale_filter(caps):
    """Compression was 46% of all request time on a 2-vCPU box; the preset and
    the no-upscale filter are the two knobs that cut it without changing the
    output size."""
    ffmpeg = FakeFfmpeg()
    with (
        patch.object(media_handler, "_get_video_duration", new=AsyncMock(return_value=30.0)),
        patch.object(media_handler.asyncio, "create_subprocess_exec", new=ffmpeg),
    ):
        result = await compress_video(b"x" * MB, 10 * MB, max_height=480)

    assert result == b"o" * 1024
    assert ffmpeg.argv[ffmpeg.argv.index("-preset") + 1] == "veryfast"
    assert ffmpeg.argv[ffmpeg.argv.index("-vf") + 1] == scale_filter(480)


# ---------------------------------------------------------------------------
# _shrink_video: two-tier policy
# ---------------------------------------------------------------------------


class FakeCompressor:
    """Stands in for compress_video; answers each (target, scale) from a table
    and records the calls so tests can assert on the pass order."""

    def __init__(self, outcomes: dict[tuple[int, int], bytes | None]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[int, int, int]] = []

    async def __call__(
        self, data: bytes, target_bytes: int, max_height: int = 720, min_video_bps: int = 0
    ) -> bytes | None:
        self.calls.append((target_bytes, max_height, min_video_bps))
        return self._outcomes.get((target_bytes, max_height))


SOFT = 10 * MB
HARD = 50 * MB


@pytest.mark.asyncio
async def test_shrink_uses_720p_soft_pass_when_it_fits(caps):
    compressor = FakeCompressor({(SOFT, 720): b"s" * 9 * MB})
    with patch.object(media_handler, "compress_video", new=compressor):
        result = await _shrink_video(b"v" * 40 * MB, SOFT, HARD)

    assert len(result) == 9 * MB
    assert [c[:2] for c in compressor.calls] == [(SOFT, 720)]


@pytest.mark.asyncio
async def test_shrink_passes_a_lower_floor_to_the_480p_pass(caps):
    compressor = FakeCompressor({(SOFT, 480): b"s" * 9 * MB})
    with patch.object(media_handler, "compress_video", new=compressor):
        result = await _shrink_video(b"v" * 40 * MB, SOFT, HARD)

    assert len(result) == 9 * MB
    assert compressor.calls == [(SOFT, 720, 500_000), (SOFT, 480, 300_000)]


@pytest.mark.asyncio
async def test_shrink_keeps_original_under_send_cap_when_floor_blocks_soft_target(caps):
    """Re-encoding a 40MB clip to a 50MB target would only lose quality, so the
    original goes out as-is (no auto-download, but watchable)."""
    compressor = FakeCompressor({})
    with patch.object(media_handler, "compress_video", new=compressor):
        result = await _shrink_video(b"v" * 40 * MB, SOFT, HARD)

    assert len(result) == 40 * MB
    assert [c[0] for c in compressor.calls] == [SOFT, SOFT]


@pytest.mark.asyncio
async def test_shrink_falls_back_to_send_cap_for_oversized_original(caps):
    """Regression for the 80MB Twitter videos: when 10MB would breach the
    floor, aim at the 50MB send cap instead of dropping the video."""
    compressor = FakeCompressor({(HARD, 720): b"s" * 45 * MB})
    with patch.object(media_handler, "compress_video", new=compressor):
        result = await _shrink_video(b"v" * 80 * MB, SOFT, HARD)

    assert len(result) == 45 * MB
    assert [c[:2] for c in compressor.calls] == [
        (SOFT, 720),
        (SOFT, 480),
        (HARD, 720),
    ]


# ---------------------------------------------------------------------------
# ensure_within_limit: nothing over the send cap reaches Telegram
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_within_limit_drops_video_still_over_send_cap(caps):
    async def still_huge(data: bytes, soft: int, hard: int) -> bytes:
        return data

    with (
        patch.object(media_handler, "_check_ffmpeg", return_value=True),
        patch.object(media_handler, "_shrink_video", new=still_huge),
    ):
        result = await ensure_within_limit([_video(80)], SOFT, hard_limit_bytes=HARD)

    assert result == []


@pytest.mark.asyncio
async def test_ensure_within_limit_keeps_compressed_video(caps):
    async def shrink(data: bytes, soft: int, hard: int) -> bytes:
        return b"s" * 9 * MB

    item = _video(80)
    with (
        patch.object(media_handler, "_check_ffmpeg", return_value=True),
        patch.object(media_handler, "_shrink_video", new=shrink),
    ):
        result = await ensure_within_limit([item], SOFT, hard_limit_bytes=HARD)

    assert result == [item]
    assert len(item.data) == 9 * MB


@pytest.mark.asyncio
async def test_ensure_within_limit_drops_oversized_video_without_ffmpeg(caps):
    with patch.object(media_handler, "_check_ffmpeg", return_value=False):
        result = await ensure_within_limit([_video(80)], SOFT, hard_limit_bytes=HARD)

    assert result == []


@pytest.mark.asyncio
async def test_ensure_within_limit_leaves_small_items_alone(caps):
    item = _video(5)
    result = await ensure_within_limit([item], SOFT, hard_limit_bytes=HARD)
    assert result == [item]
