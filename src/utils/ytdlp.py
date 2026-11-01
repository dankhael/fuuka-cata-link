"""Helper for running yt-dlp to download media directly to memory.

Many platforms (TikTok, Instagram, Facebook) use signed/temporary URLs that
can't be downloaded separately after extraction. This module downloads the
file via yt-dlp into a temp directory and reads the bytes back.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.config import settings
from src.utils import cookie_jar
from src.utils.proxy import redact_proxy_credentials

logger = structlog.get_logger()


@dataclass
class YtdlpResult:
    """Result from a yt-dlp extraction + download."""

    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    ext: str = "mp4"
    data: bytes | None = None  # downloaded file content
    thumbnail_url: str | None = None
    is_video: bool = True
    is_animation: bool = False  # True for GIFs
    duration: float | None = None  # seconds
    # True when the media was dropped for exceeding MAX_DOWNLOAD_SIZE_MB (either
    # yt-dlp aborted on --max-filesize or the merged file came out over it).
    # The Telegram send cap is enforced later, after compression.
    # Lets callers tell "too big to send" apart from a genuine failure.
    exceeds_size_limit: bool = False


# Cap yt-dlp's internal retries. Its defaults retry each HTTP step many times,
# stacking socket timeouts into multi-minute hangs over a flaky proxy (DAN-80).
# One retry is plenty; _run_ytdlp's wall-clock ceiling is the real backstop.
_FAST_FAIL_ARGS = [
    "--retries",
    "1",
    "--extractor-retries",
    "1",
    "--fragment-retries",
    "1",
    "--socket-timeout",
]


def _fast_fail_args() -> list[str]:
    return [*_FAST_FAIL_ARGS, str(settings.download_timeout_seconds)]


# -S sort for the retry: best stream whose shortest side is <= 480px, so it
# picks the same quality for portrait reels and landscape videos.
_SMALL_FORMAT_SORT = "res:480"

# yt-dlp saves each stream of a merged download as "<name>.f<format_id>.<ext>"
# and only writes "<name>.<ext>" once ffmpeg merges them. A lone part means the
# merge never ran: --max-filesize aborted the video stream but yt-dlp still
# fetched the audio, and that bare .m4a reached Telegram as a "photo"
# (IMAGE_PROCESS_FAILED on a 10-min Instagram reel).
_UNMERGED_PART = re.compile(r"^media\.f.+\.[^.]+$")

# yt-dlp exits 0 when --max-filesize aborts a stream; this line is the only trace.
_SIZE_ABORT = re.compile(rb"File is larger than max-filesize \((\d+) bytes")


def expected_filesize(info: dict) -> int | None:
    """Best available size estimate (bytes) for what yt-dlp would download.

    Reads the metadata dict from :func:`ytdlp_info`, so callers can drop
    oversized media *before* paying for the transfer. Returns ``None`` when
    the extractor exposes no size at all.

    >>> expected_filesize({"filesize_approx": 12_000_000})
    12000000
    """
    parts = [
        fmt.get("filesize") or fmt.get("filesize_approx")
        for fmt in info.get("requested_formats") or []
    ]
    if parts and all(parts):
        return sum(parts)
    return info.get("filesize") or info.get("filesize_approx")


async def _run_ytdlp(cmd: list[str], *, what: str) -> tuple[int, bytes, bytes]:
    """Run a yt-dlp command, killing it if it exceeds ``ytdlp_timeout_seconds``.

    yt-dlp's --socket-timeout/--retries only bound individual HTTP attempts; a
    flaky proxy can still stack them into multi-minute hangs (DAN-80), so we
    enforce one wall-clock ceiling and kill the process if it is exceeded.
    Returns (returncode, stdout, stderr).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=settings.ytdlp_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"yt-dlp {what} timed out after {settings.ytdlp_timeout_seconds}s"
        ) from None
    return proc.returncode or 0, stdout, stderr


async def _cookie_args(tmpdir: str, cookies_file: str | None) -> tuple[list[str], str | None]:
    """yt-dlp auth flags for one invocation, plus the private jar to check in.

    The jar is a per-run copy of the live jar (``cookie_jar.checkout``): yt-dlp
    rewrites it with rotated cookies on exit, two concurrent runs must not
    share one file, and the rotation has to be merged back afterwards or
    YouTube invalidates the session within a few requests.
    """
    private_jar = await cookie_jar.checkout(tmpdir, cookies_file)
    if private_jar:
        return ["--cookies", private_jar], private_jar
    if settings.cookies_from_browser:
        return ["--cookies-from-browser", settings.cookies_from_browser], None
    return [], None


async def ytdlp_info(
    url: str,
    extra_args: list[str] | None = None,
    proxy: str | None = None,
    cookies_file: str | None = None,
) -> dict:
    """Run yt-dlp --dump-json to get metadata without downloading.

    Pass the same *cookies_file* as the download: YouTube's bot-gate answers
    the metadata request too, so an unauthenticated probe fails ("Sign in to
    confirm you're not a bot") even when the download would have succeeded.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = ["yt-dlp", "--dump-json", "--no-download", "--remote-components", "ejs:github"]
        cmd.extend(_fast_fail_args())
        cookie_flags, private_jar = await _cookie_args(tmpdir, cookies_file)
        cmd.extend(cookie_flags)
        if proxy:
            cmd.extend(["--proxy", proxy])
        if settings.ytdlp_js_runtime:
            cmd.extend(["--js-runtimes", settings.ytdlp_js_runtime])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)

        returncode, stdout, stderr = await _run_ytdlp(cmd, what="info")
        if private_jar:
            await cookie_jar.checkin(private_jar)
    if returncode != 0:
        raise RuntimeError(
            f"yt-dlp info failed: {redact_proxy_credentials(stderr.decode().strip())}"
        )

    return json.loads(stdout)


async def ytdlp_download(
    url: str,
    extra_args: list[str] | None = None,
    cookies_file: str | None = None,
    proxy: str | None = None,
) -> YtdlpResult:
    """Download media via yt-dlp directly and return bytes + metadata.

    This avoids the signed-URL problem by letting yt-dlp handle the full
    download pipeline rather than extracting a URL and fetching it ourselves.

    Pass *proxy* (e.g. ``socks5://host:1080``) to route through a residential
    proxy. yt-dlp's own error text never identifies the proxy as the culprit,
    so callers that want a direct fallback must probe it themselves with
    ``src.utils.proxy.is_proxy_reachable``.

    An over-limit download is retried once at up to 480p before it is given up
    as ``exceeds_size_limit``.

    Usage: ``result = await ytdlp_download(url, cookies_file=settings.cookies_file)``
    """
    result = await _download_once(url, extra_args, cookies_file, proxy, format_sort=None)
    if not result.exceeds_size_limit:
        return result
    # Extractors like Instagram's expose no duration or size per format, so the
    # -f filesize filter can't steer away from a 400MB 1080p stream; only the
    # HTTP response reveals it. A long reel's 358p stream still fits easily.
    logger.info("ytdlp_retry_smaller_format", url=url, format_sort=_SMALL_FORMAT_SORT)
    return await _download_once(
        url, extra_args, cookies_file, proxy, format_sort=_SMALL_FORMAT_SORT
    )


def _download_cmd(output_template: str, format_sort: str | None) -> list[str]:
    max_size = f"{settings.max_download_size_mb}M"
    format_spec = f"bv*+ba[filesize<{max_size}]/bv*+ba/b[filesize<{max_size}]/b"
    cmd = [
        "yt-dlp",
        "-o",
        output_template,
        "--no-playlist",
        "-f",
        format_spec,
        "--max-filesize",
        max_size,
        "--write-info-json",
        "--remote-components",
        "ejs:github",
        *_fast_fail_args(),
    ]
    if format_sort:
        cmd.extend(["-S", format_sort])
    return cmd


async def _download_once(
    url: str,
    extra_args: list[str] | None,
    cookies_file: str | None,
    proxy: str | None,
    *,
    format_sort: str | None,
) -> YtdlpResult:
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = _download_cmd(str(Path(tmpdir) / "media.%(ext)s"), format_sort)
        # Cookies live in a subdirectory so yt-dlp's rewritten jar doesn't get
        # picked up as a downloaded media file below.
        cookie_flags, private_jar = await _cookie_args(tmpdir, cookies_file)
        cmd.extend(cookie_flags)
        if proxy:
            cmd.extend(["--proxy", proxy])
        if settings.ytdlp_js_runtime:
            cmd.extend(["--js-runtimes", settings.ytdlp_js_runtime])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)

        returncode, stdout, stderr = await _run_ytdlp(cmd, what="download")
        if private_jar:
            await cookie_jar.checkin(private_jar)

        if returncode != 0:
            raise RuntimeError(
                f"yt-dlp download failed: {redact_proxy_credentials(stderr.decode().strip())}"
            )
        return _collect_download(Path(tmpdir), stdout)


def _collect_download(tmppath: Path, stdout: bytes) -> YtdlpResult:
    """Turn yt-dlp's output directory into a result, flagging over-limit media."""
    info_files = list(tmppath.glob("*.info.json"))
    info: dict = json.loads(info_files[0].read_text(encoding="utf-8")) if info_files else {}

    data: bytes | None = None
    ext = "mp4"
    media_file = _finished_media_file(tmppath)
    if media_file:
        ext = media_file.suffix.lstrip(".")
        data = _read_within_limit(media_file)
        exceeds_size_limit = data is None
    else:
        # --max-filesize aborts *before* writing anything and still exits 0,
        # leaving only the info json. Without this the caller can't tell an
        # over-limit video from a broken download.
        aborted_size = _aborted_size(stdout, info)
        exceeds_size_limit = aborted_size is not None
        if aborted_size is not None:
            logger.warning("ytdlp_download_aborted_too_large", size_mb=_mb(aborted_size))

    is_animation = ext in ("gif", "gifv")
    is_video = ext in ("mp4", "webm", "mkv", "mov", "avi", "flv") and not is_animation

    return YtdlpResult(
        title=info.get("title"),
        description=info.get("description"),
        uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
        ext=ext,
        data=data,
        thumbnail_url=info.get("thumbnail"),
        is_video=is_video,
        is_animation=is_animation,
        duration=info.get("duration"),
        exceeds_size_limit=exceeds_size_limit,
    )


def _finished_media_file(tmppath: Path) -> Path | None:
    """The completed download, skipping the info json and unmerged stream parts."""
    for f in tmppath.iterdir():
        if f.is_file() and not f.name.endswith(".info.json") and not _UNMERGED_PART.match(f.name):
            return f
    return None


def _read_within_limit(media_file: Path) -> bytes | None:
    file_size = media_file.stat().st_size
    if file_size > settings.max_download_size_mb * 1024 * 1024:
        logger.warning("ytdlp_file_too_large", size_mb=_mb(file_size))
        return None
    return media_file.read_bytes()


def _aborted_size(stdout: bytes, info: dict) -> int | None:
    """Bytes of the stream yt-dlp refused on --max-filesize, or None if it didn't.

    The size from yt-dlp's own abort line wins; the metadata estimate covers
    output that lacks it (quiet runs, a future rewording upstream).
    """
    match = _SIZE_ABORT.search(stdout)
    if match:
        return int(match.group(1))
    predicted = expected_filesize(info)
    if predicted is not None and predicted > settings.max_download_size_mb * 1024 * 1024:
        return predicted
    return None


def _mb(size_bytes: int) -> float:
    return round(size_bytes / 1024 / 1024, 1)
