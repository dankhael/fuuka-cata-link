"""Helper for running gallery-dl to download images directly to memory.

gallery-dl handles image-only posts that yt-dlp cannot process.
It supports Instagram, Facebook, and many other image-hosting platforms.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from src.config import settings

logger = structlog.get_logger()

_VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "avi", "flv"}


@dataclass
class GalleryDlFile:
    """A single file downloaded by gallery-dl."""

    data: bytes
    ext: str
    is_video: bool = False


@dataclass
class GalleryDlResult:
    """Result from a gallery-dl extraction + download."""

    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    files: list[GalleryDlFile] = field(default_factory=list)


async def gallery_dl_download(
    url: str,
    cookies_file: str | None = None,
) -> GalleryDlResult:
    """Download media via gallery-dl and return bytes + metadata.

    gallery-dl handles image posts that yt-dlp cannot process.
    Returns all images/videos from the post (supports carousels).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            "gallery-dl",
            "--dest", tmpdir,
            "--no-mtime",
            "--write-metadata",
            "--range", "1-10",
        ]
        if cookies_file:
            cmd.extend(["--cookies", cookies_file])
        cmd.append(url)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"gallery-dl download failed: {stderr.decode().strip()}")

        tmppath = Path(tmpdir)

        # Collect metadata from .json sidecar files
        metadata: dict = {}
        for json_file in tmppath.rglob("*.json"):
            try:
                metadata = json.loads(json_file.read_text(encoding="utf-8"))
                break
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

        # Collect downloaded media files (exclude .json metadata)
        files: list[GalleryDlFile] = []
        media_files = sorted(
            f for f in tmppath.rglob("*")
            if f.is_file() and f.suffix.lstrip(".") != "json"
        )

        for media_file in media_files:
            file_size = media_file.stat().st_size
            if file_size > settings.max_file_size_mb * 1024 * 1024:
                logger.warning(
                    "gallery_dl_file_too_large",
                    size_mb=round(file_size / 1024 / 1024, 1),
                )
                continue
            if file_size < 1024:
                continue

            ext = media_file.suffix.lstrip(".")
            files.append(
                GalleryDlFile(
                    data=media_file.read_bytes(),
                    ext=ext,
                    is_video=ext in _VIDEO_EXTS,
                )
            )

        if not files:
            raise RuntimeError("gallery-dl downloaded no usable media files")

        return GalleryDlResult(
            title=metadata.get("description") or metadata.get("title"),
            description=metadata.get("description"),
            uploader=metadata.get("username") or metadata.get("uploader"),
            files=files,
        )
