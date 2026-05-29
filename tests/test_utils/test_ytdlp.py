from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.ytdlp import _run_ytdlp


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
