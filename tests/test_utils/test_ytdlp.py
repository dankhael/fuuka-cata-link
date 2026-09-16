from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.utils.ytdlp import _run_ytdlp, expected_filesize, ytdlp_download, ytdlp_info


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


class FakeRecordingRun:
    """Captures the yt-dlp argv and leaves a small media file behind."""

    def __init__(self) -> None:
        self.cmd: list[str] = []

    async def __call__(self, cmd: list[str], *, what: str) -> tuple[int, bytes, bytes]:
        self.cmd = cmd
        outdir = Path(cmd[cmd.index("-o") + 1]).parent
        (outdir / "media.mp4").write_bytes(b"v" * 1024)
        return 0, b"", b""


@pytest.mark.asyncio
async def test_download_caps_yt_dlp_at_the_download_ceiling_not_the_send_cap():
    """Regression: --max-filesize used to be the 50MB Telegram cap, so yt-dlp
    aborted on every 60–80MB video that ffmpeg could have shrunk."""
    run = FakeRecordingRun()
    with patch("src.utils.ytdlp._run_ytdlp", new=run), patch("src.utils.ytdlp.settings") as cfg:
        cfg.max_download_size_mb = 200
        cfg.max_file_size_mb = 50
        cfg.download_timeout_seconds = 30
        cfg.cookies_from_browser = None
        cfg.ytdlp_js_runtime = None
        result = await ytdlp_download("https://youtu.be/anyvideo")

    assert result.data == b"v" * 1024
    assert run.cmd[run.cmd.index("--max-filesize") + 1] == "200M"
    assert "50M" not in " ".join(run.cmd)


class FakeInfoRun:
    """Captures the yt-dlp argv of a --dump-json probe and returns metadata."""

    def __init__(self) -> None:
        self.cmd: list[str] = []

    async def __call__(self, cmd: list[str], *, what: str) -> tuple[int, bytes, bytes]:
        self.cmd = cmd
        return 0, json.dumps({"duration": 42}).encode(), b""


@pytest.fixture
def cookie_state_dir(tmp_path):
    with patch("src.utils.cookie_jar.settings") as cfg:
        cfg.cookies_state_dir = str(tmp_path / "state")
        yield tmp_path / "state"


@pytest.mark.asyncio
async def test_info_probe_sends_a_writable_copy_of_the_cookie_jar(tmp_path, cookie_state_dir):
    """Regression: the probe ran without cookies, so YouTube's bot-gate failed
    every link at the metadata step even when the download would have passed."""
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    run = FakeInfoRun()

    with patch("src.utils.ytdlp._run_ytdlp", new=run):
        info = await ytdlp_info("https://youtu.be/gated", cookies_file=str(jar))

    assert info == {"duration": 42}
    passed_jar = Path(run.cmd[run.cmd.index("--cookies") + 1])
    assert passed_jar != jar, "yt-dlp rewrites the jar; the mounted original must stay untouched"
    assert passed_jar.name == "cookies.txt"


class FakeRotatingRun:
    """A yt-dlp run that, like the real one, saves rotated cookies into the
    --cookies file it was handed before exiting."""

    async def __call__(self, cmd: list[str], *, what: str) -> tuple[int, bytes, bytes]:
        jar = Path(cmd[cmd.index("--cookies") + 1])
        jar.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t2000000000\t__Secure-1PSIDTS\trotated\n"
        )
        return 0, json.dumps({"duration": 1}).encode(), b""


@pytest.mark.asyncio
async def test_rotated_cookies_survive_into_the_next_run(tmp_path, cookie_state_dir):
    """Regression: the rotated jar was discarded with the temp dir, so the
    mounted original went stale and YouTube reported the cookies as rotated."""
    jar = tmp_path / "cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t2000000000\t__Secure-1PSIDTS\toriginal\n"
    )
    run = FakeInfoRun()

    with patch("src.utils.ytdlp._run_ytdlp", new=FakeRotatingRun()):
        await ytdlp_info("https://youtu.be/first", cookies_file=str(jar))
    with patch("src.utils.ytdlp._run_ytdlp", new=run):
        await ytdlp_info("https://youtu.be/second", cookies_file=str(jar))

    handed_to_second_run = Path(run.cmd[run.cmd.index("--cookies") + 1])
    # the second run's private copy was already deleted with its temp dir;
    # the live jar is what it was copied from
    assert "rotated" in (cookie_state_dir / "cookies.txt.live").read_text()
    assert handed_to_second_run.name == "cookies.txt"
    assert "original" in jar.read_text()


@pytest.mark.asyncio
async def test_info_probe_without_cookies_sends_no_cookie_flag():
    run = FakeInfoRun()
    with patch("src.utils.ytdlp._run_ytdlp", new=run), patch("src.utils.ytdlp.settings") as cfg:
        cfg.cookies_from_browser = None
        cfg.ytdlp_js_runtime = None
        cfg.download_timeout_seconds = 30
        await ytdlp_info("https://youtu.be/open")

    assert "--cookies" not in run.cmd
    assert "--cookies-from-browser" not in run.cmd
