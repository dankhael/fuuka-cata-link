from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
from io import BytesIO
from pathlib import Path

import aiohttp
import structlog
from PIL import Image

from src.config import settings
from src.scrapers.base import MediaItem, MediaType

logger = structlog.get_logger()

_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


async def download_media(
    items: list[MediaItem],
    session: aiohttp.ClientSession | None = None,
) -> list[MediaItem]:
    """Download media items concurrently and populate their `data` field.

    Items exceeding MAX_FILE_SIZE_MB are skipped with a warning.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    sem = asyncio.Semaphore(settings.concurrent_downloads)

    async def _fetch(item: MediaItem) -> None:
        async with sem:
            try:
                async with session.get(
                    item.url,
                    timeout=aiohttp.ClientTimeout(total=settings.download_timeout_seconds),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    if len(data) > _MAX_BYTES:
                        logger.warning(
                            "media_too_large",
                            url=item.url,
                            size_mb=round(len(data) / 1024 / 1024, 1),
                        )
                        return
                    item.data = data
            except Exception as exc:
                logger.error("media_download_failed", url=item.url, error=str(exc))

    start = time.monotonic()
    try:
        await asyncio.gather(*[_fetch(item) for item in items])
    finally:
        if own_session:
            await session.close()

    result = [item for item in items if item.data is not None]
    logger.info("media_downloaded", count=len(result), duration_ms=int((time.monotonic() - start) * 1000))
    return result


def optimize_image(data: bytes, max_dimension: int = 1920, quality: int = 85) -> bytes:
    """Compress an image while preserving reasonable quality."""
    img = Image.open(BytesIO(data))
    img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
    buf = BytesIO()
    fmt = "JPEG" if img.mode == "RGB" else "PNG"
    img.save(buf, format=fmt, quality=quality, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Video compression via ffmpeg
# ---------------------------------------------------------------------------

_FFMPEG_AVAILABLE: bool | None = None


def _check_ffmpeg() -> bool:
    """Check if ffmpeg and ffprobe are available on PATH (cached)."""
    global _FFMPEG_AVAILABLE
    if _FFMPEG_AVAILABLE is None:
        _FFMPEG_AVAILABLE = (
            shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
        )
        if not _FFMPEG_AVAILABLE:
            logger.warning("ffmpeg_not_found", msg="Video compression disabled")
    return _FFMPEG_AVAILABLE


async def _get_video_duration(path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    info = json.loads(stdout)
    return float(info["format"]["duration"])


async def compress_video(data: bytes, target_bytes: int, scale: str = "-2:720") -> bytes | None:
    """Re-encode video to fit within *target_bytes* using ffmpeg.

    Returns compressed bytes, or None if compression fails.
    *scale* is the ffmpeg scale filter value (e.g. "-2:720" for 720p).
    """
    tmp_dir = tempfile.mkdtemp(prefix="compress_")
    try:
        input_path = Path(tmp_dir) / "input.mp4"
        output_path = Path(tmp_dir) / "output.mp4"
        input_path.write_bytes(data)

        duration = await _get_video_duration(input_path)
        if duration <= 0:
            logger.warning("compress_video_bad_duration", duration=duration)
            return None

        # Target bitrate: leave 128kbps headroom for audio, apply 0.9 safety factor
        audio_bps = 128_000
        target_video_bps = int(((target_bytes * 8) / duration - audio_bps) * 0.9)
        if target_video_bps < 100_000:
            logger.warning("compress_video_bitrate_too_low", target_bps=target_video_bps)
            return None

        ffmpeg_start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-c:v", "libx264",
            "-b:v", str(target_video_bps),
            "-maxrate", str(target_video_bps),
            "-bufsize", str(target_video_bps * 2),
            "-vf", f"scale={scale}",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("ffmpeg_failed", returncode=proc.returncode, stderr=stderr.decode()[-500:])
            return None

        result = output_path.read_bytes()
        logger.info(
            "video_compressed",
            original_mb=round(len(data) / 1024 / 1024, 1),
            compressed_mb=round(len(result) / 1024 / 1024, 1),
            scale=scale,
            duration_ms=int((time.monotonic() - ffmpeg_start) * 1000),
        )
        return result
    except Exception as exc:
        logger.error("compress_video_error", error=str(exc))
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def ensure_within_limit(items: list[MediaItem], limit_bytes: int) -> list[MediaItem]:
    """Compress media items that exceed *limit_bytes*.

    - Videos/animations are re-encoded via ffmpeg (720p, then 480p fallback).
    - Images are optimized via Pillow.
    - If compression is unavailable or fails, the item is kept as-is.
    """
    if limit_bytes <= 0:
        return items

    for item in items:
        if item.data is None or len(item.data) <= limit_bytes:
            continue

        original_mb = round(len(item.data) / 1024 / 1024, 1)

        if item.media_type in (MediaType.VIDEO, MediaType.ANIMATION):
            if not _check_ffmpeg():
                continue

            # First pass: 720p
            compressed = await compress_video(item.data, limit_bytes, scale="-2:720")
            if compressed and len(compressed) <= limit_bytes:
                item.data = compressed
                continue

            # Second pass: 480p
            logger.info("compress_video_retry_480p", original_mb=original_mb)
            compressed = await compress_video(item.data, limit_bytes, scale="-2:480")
            if compressed and len(compressed) <= limit_bytes:
                item.data = compressed
                continue

            # Use best result even if still over limit
            if compressed and len(compressed) < len(item.data):
                item.data = compressed
            logger.warning(
                "compress_video_still_over_limit",
                original_mb=original_mb,
                final_mb=round(len(item.data) / 1024 / 1024, 1),
                limit_mb=round(limit_bytes / 1024 / 1024, 1),
            )

        elif item.media_type == MediaType.IMAGE:
            optimized = optimize_image(item.data)
            if len(optimized) <= limit_bytes:
                item.data = optimized
            else:
                # Try more aggressive compression
                optimized = optimize_image(item.data, max_dimension=1280, quality=70)
                if len(optimized) < len(item.data):
                    item.data = optimized
                logger.warning(
                    "image_still_over_limit",
                    original_mb=original_mb,
                    final_mb=round(len(item.data) / 1024 / 1024, 1),
                )

    return items


def is_image(item: MediaItem) -> bool:
    return item.media_type == MediaType.IMAGE


def is_video(item: MediaItem) -> bool:
    return item.media_type == MediaType.VIDEO


def is_animation(item: MediaItem) -> bool:
    return item.media_type == MediaType.ANIMATION
