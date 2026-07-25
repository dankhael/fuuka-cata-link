"""Helper for running yt-dlp to download media directly to memory.

Many platforms (TikTok, Instagram, Facebook) use signed/temporary URLs that
can't be downloaded separately after extraction. This module downloads the
file via yt-dlp into a temp directory and reads the bytes back.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.config import settings
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
    # True when the media was dropped for exceeding MAX_FILE_SIZE_MB (either
    # yt-dlp aborted on --max-filesize or the merged file came out over it).
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


async def ytdlp_info(
    url: str, extra_args: list[str] | None = None, proxy: str | None = None
) -> dict:
    """Run yt-dlp --dump-json to get metadata without downloading."""
    cmd = ["yt-dlp", "--dump-json", "--no-download", "--remote-components", "ejs:github"]
    cmd.extend(_fast_fail_args())
    if proxy:
        cmd.extend(["--proxy", proxy])
    if settings.ytdlp_js_runtime:
        cmd.extend(["--js-runtimes", settings.ytdlp_js_runtime])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)

    returncode, stdout, stderr = await _run_ytdlp(cmd, what="info")
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
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = str(Path(tmpdir) / "media.%(ext)s")
        max_size = f"{settings.max_file_size_mb}M"
        format_spec = f"bv*+ba[filesize<{max_size}]/bv*+ba/b[filesize<{max_size}]/b"
        cmd = [
            "yt-dlp",
            "-o",
            output_template,
            "--no-playlist",
            "-f",
            format_spec,
            "--max-filesize",
            f"{settings.max_file_size_mb}M",
            "--write-info-json",
            "--remote-components",
            "ejs:github",
            *_fast_fail_args(),
        ]
        if cookies_file:
            # Copy cookies to a separate subdirectory so yt-dlp can save updated
            # cookies without polluting the media output directory
            cookies_dir = Path(tmpdir) / "_cookies"
            cookies_dir.mkdir()
            writable_cookies = str(cookies_dir / "cookies.txt")
            shutil.copy2(cookies_file, writable_cookies)
            cmd.extend(["--cookies", writable_cookies])
        elif settings.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", settings.cookies_from_browser])
        if proxy:
            cmd.extend(["--proxy", proxy])
        if settings.ytdlp_js_runtime:
            cmd.extend(["--js-runtimes", settings.ytdlp_js_runtime])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)

        returncode, stdout, stderr = await _run_ytdlp(cmd, what="download")

        if returncode != 0:
            raise RuntimeError(
                f"yt-dlp download failed: {redact_proxy_credentials(stderr.decode().strip())}"
            )

        # Find the downloaded media file and info json
        tmppath = Path(tmpdir)
        info_files = list(tmppath.glob("*.info.json"))
        media_files = [
            f for f in tmppath.iterdir() if f.is_file() and not f.name.endswith(".info.json")
        ]

        info: dict = {}
        if info_files:
            info = json.loads(info_files[0].read_text(encoding="utf-8"))

        limit_bytes = settings.max_file_size_mb * 1024 * 1024
        data: bytes | None = None
        ext = "mp4"
        exceeds_size_limit = False
        if media_files:
            media_file = media_files[0]
            ext = media_file.suffix.lstrip(".")
            file_size = media_file.stat().st_size
            if file_size <= limit_bytes:
                data = media_file.read_bytes()
            else:
                exceeds_size_limit = True
                logger.warning("ytdlp_file_too_large", size_mb=round(file_size / 1024 / 1024, 1))
        else:
            # --max-filesize aborts *before* writing anything and still exits 0,
            # leaving only the info json. Without this the caller can't tell an
            # over-limit video from a broken download.
            predicted = expected_filesize(info)
            exceeds_size_limit = predicted is not None and predicted > limit_bytes
            if exceeds_size_limit:
                logger.warning(
                    "ytdlp_download_aborted_too_large",
                    size_mb=round(predicted / 1024 / 1024, 1),
                )

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
