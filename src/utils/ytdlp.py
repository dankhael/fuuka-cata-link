"""Helper for running yt-dlp to download media directly to memory.

Many platforms (TikTok, Instagram, Facebook) use signed/temporary URLs that
can't be downloaded separately after extraction. This module downloads the
file via yt-dlp into a temp directory and reads the bytes back.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from src.config import settings

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


async def ytdlp_info(url: str, extra_args: list[str] | None = None) -> dict:
    """Run yt-dlp --dump-json to get metadata without downloading."""
    cmd = ["yt-dlp", "--dump-json", "--no-download"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp info failed: {stderr.decode().strip()}")

    return json.loads(stdout)


async def ytdlp_download(
    url: str,
    extra_args: list[str] | None = None,
    cookies_file: str | None = None,
) -> YtdlpResult:
    """Download media via yt-dlp directly and return bytes + metadata.

    This avoids the signed-URL problem by letting yt-dlp handle the full
    download pipeline rather than extracting a URL and fetching it ourselves.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = str(Path(tmpdir) / "media.%(ext)s")
        cmd = [
            "yt-dlp",
            "-o", output_template,
            "--no-playlist",
            "-f", f"best[filesize<{settings.max_file_size_mb}M]/best",
            "--max-filesize", f"{settings.max_file_size_mb}M",
            "--write-info-json",
            "--socket-timeout", str(settings.download_timeout_seconds),
        ]
        if cookies_file:
            cmd.extend(["--cookies", cookies_file])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp download failed: {stderr.decode().strip()}")

        # Find the downloaded media file and info json
        tmppath = Path(tmpdir)
        info_files = list(tmppath.glob("*.info.json"))
        media_files = [
            f for f in tmppath.iterdir()
            if f.is_file() and not f.name.endswith(".info.json")
        ]

        info: dict = {}
        if info_files:
            info = json.loads(info_files[0].read_text(encoding="utf-8"))

        data: bytes | None = None
        ext = "mp4"
        if media_files:
            media_file = media_files[0]
            ext = media_file.suffix.lstrip(".")
            file_size = media_file.stat().st_size
            if file_size <= settings.max_file_size_mb * 1024 * 1024:
                data = media_file.read_bytes()
            else:
                logger.warning("ytdlp_file_too_large", size_mb=round(file_size / 1024 / 1024, 1))

        is_video = ext in ("mp4", "webm", "mkv", "mov", "avi", "flv")

        return YtdlpResult(
            title=info.get("title"),
            description=info.get("description"),
            uploader=info.get("uploader") or info.get("channel") or info.get("creator"),
            ext=ext,
            data=data,
            thumbnail_url=info.get("thumbnail"),
            is_video=is_video,
        )
