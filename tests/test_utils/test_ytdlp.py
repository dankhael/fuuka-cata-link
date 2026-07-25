from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.ytdlp import _run_ytdlp, expected_filesize, ytdlp_download


@pytest.mark.asyncio
async def test_run_ytdlp_returns_output_on_success():
    proc = MagicMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"out", b"err"))

    with patch("src.utils.ytdlp.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        returncode, stdout, stderr = await _run_ytdlp(["yt-dlp", "x"], what="info")

    assert (returncode, stdout, stderr) == (0, b"out", b"err")


@pytest.mark.asyncio
async def test_run_ytdlp_kills_process_on_timeout():
    """A hung yt-dlp (e.g. flaky proxy) is killed at the wall-clock ceiling
    instead of stacking retries into a multi-minute hang (DAN-80)."""
    proc = MagicMock()
    proc.communicate = MagicMock()  # don't create an un-awaited coroutine
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("src.utils.ytdlp.asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        with patch(
            "src.utils.ytdlp.asyncio.wait_for",
            new=AsyncMock(side_effect=asyncio.TimeoutError),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                await _run_ytdlp(["yt-dlp", "x"], what="download")

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


def test_expected_filesize_sums_the_merged_streams():
    """A merged video+audio download costs the sum of both streams, so the
    per-stream sizes must win over the top-level estimate."""
    info = {
        "filesize_approx": 1,
        "requested_formats": [{"filesize": 40}, {"filesize_approx": 2}],
    }

    assert expected_filesize(info) == 42


def test_expected_filesize_is_none_when_unknown():
    assert expected_filesize({"requested_formats": [{"filesize": None}]}) is None
    assert expected_filesize({}) is None


class FakeAbortedRun:
    """A yt-dlp run that aborted on --max-filesize: exit 0, no media file, and
    only the info json left in the output directory."""

    def __init__(self, info: dict) -> None:
        self._info = info

    async def __call__(self, cmd: list[str], *, what: str) -> tuple[int, bytes, bytes]:
        outdir = Path(cmd[cmd.index("-o") + 1]).parent
        (outdir / "media.info.json").write_text(json.dumps(self._info), encoding="utf-8")
        return 0, b"", b""


@pytest.mark.asyncio
async def test_download_flags_an_oversized_abort():
    """yt-dlp exits 0 after aborting on --max-filesize, so the empty result is
    indistinguishable from a broken download without this flag."""
    info = {"title": "huge", "duration": 60, "filesize_approx": 900 * 1024 * 1024}

    with patch("src.utils.ytdlp._run_ytdlp", new=FakeAbortedRun(info)):
        result = await ytdlp_download("https://youtu.be/hugevideo")

    assert result.exceeds_size_limit
    assert result.data is None
    assert result.title == "huge"


@pytest.mark.asyncio
async def test_download_without_media_is_not_flagged_as_oversized():
    """An empty result with no size evidence stays a plain failure, so the
    caller still reports it instead of silently dropping the link."""
    with patch("src.utils.ytdlp._run_ytdlp", new=FakeAbortedRun({"title": "broken"})):
        result = await ytdlp_download("https://youtu.be/broken")

    assert not result.exceeds_size_limit
    assert result.data is None
