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

# Absolute floor for any re-encode; below this nothing is watchable regardless
# of resolution, so the encoder gives up rather than produce garbage.
_ABSOLUTE_MIN_VIDEO_BPS = 100_000


def _download_cap_bytes() -> int:
    return settings.max_download_size_mb * 1024 * 1024


def _mb(size_bytes: int) -> float:
    return round(size_bytes / 1024 / 1024, 1)


async def download_media(
    items: list[MediaItem],
    session: aiohttp.ClientSession | None = None,
) -> list[MediaItem]:
    """Download media items concurrently and populate their `data` field.

    Items exceeding MAX_DOWNLOAD_SIZE_MB are skipped with a warning — before
    the transfer when the server announces a Content-Length, after it
    otherwise. Fitting the Telegram send cap is `ensure_within_limit`'s job.
    """
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    sem = asyncio.Semaphore(settings.concurrent_downloads)
    cap = _download_cap_bytes()

    async def _fetch(item: MediaItem) -> None:
        async with sem:
            try:
                async with session.get(
                    item.url,
                    timeout=aiohttp.ClientTimeout(total=settings.download_timeout_seconds),
                ) as resp:
                    resp.raise_for_status()
                    announced = resp.content_length
                    if announced is not None and announced > cap:
                        logger.warning("media_too_large", url=item.url, size_mb=_mb(announced))
                        return
                    data = await resp.read()
                    if len(data) > cap:
                        logger.warning("media_too_large", url=item.url, size_mb=_mb(len(data)))
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
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info("media_downloaded", count=len(result), duration_ms=duration_ms)
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
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    info = json.loads(stdout)
    return float(info["format"]["duration"])


def target_video_bitrate(target_bytes: int, duration_seconds: float) -> int:
    """Video bitrate (bps) that lands a *duration_seconds* clip at *target_bytes*.

    Leaves 128kbps for audio and applies a 0.9 safety factor so container
    overhead and rate-control overshoot don't push the file over the target.

    >>> target_video_bitrate(10 * 1024 * 1024, 60)
    1143091
    """
    audio_bps = 128_000
    return int(((target_bytes * 8) / duration_seconds - audio_bps) * 0.9)


def scale_filter(max_height: int) -> str:
    """ffmpeg scale filter capping the height at *max_height* without upscaling.

    A fixed ``scale=-2:720`` used to inflate 480p/540p sources to 720p, which
    costs encode time and spends the bitrate budget on invented pixels. The
    height is rounded down to even because libx264 rejects odd dimensions.

    >>> scale_filter(720)
    "scale=-2:'2*trunc(min(720,ih)/2)'"
    """
    return f"scale=-2:'2*trunc(min({max_height},ih)/2)'"


async def compress_video(
    data: bytes,
    target_bytes: int,
    max_height: int = 720,
    min_video_bps: int = _ABSOLUTE_MIN_VIDEO_BPS,
) -> bytes | None:
    """Re-encode video to fit within *target_bytes* using ffmpeg.

    Returns compressed bytes, or None if compression fails or the bitrate
    needed to hit the target falls below *min_video_bps* (the caller's
    quality floor). Output height is capped at *max_height* (never upscaled).
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

        target_video_bps = target_video_bitrate(target_bytes, duration)
        if target_video_bps < min_video_bps:
            logger.info(
                "compress_video_bitrate_too_low",
                target_bps=target_video_bps,
                floor_bps=min_video_bps,
                max_height=max_height,
                target_mb=_mb(target_bytes),
            )
            return None

        ffmpeg_start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            settings.video_encode_preset,
            "-b:v",
            str(target_video_bps),
            "-maxrate",
            str(target_video_bps),
            "-bufsize",
            str(target_video_bps * 2),
            "-vf",
            scale_filter(max_height),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
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
            original_mb=_mb(len(data)),
            compressed_mb=_mb(len(result)),
            max_height=max_height,
            duration_ms=int((time.monotonic() - ffmpeg_start) * 1000),
        )
        return result
    except Exception as exc:
        logger.error("compress_video_error", error=str(exc))
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _soft_passes() -> list[tuple[int, int]]:
    """(max height, bitrate floor) attempts for the auto-download target, in order.

    480p has ~44% of 720p's pixels, so 60% of the 720p floor still gives it
    more bits per pixel — the second pass rescues clips the first can't.
    """
    floor_720p = settings.min_video_bitrate_kbps * 1000
    return [(720, floor_720p), (480, int(floor_720p * 0.6))]


async def _shrink_video(data: bytes, soft_limit: int, hard_limit: int) -> bytes:
    """Best re-encode of *data* under the two-tier size policy.

    Tier 1 aims at *soft_limit* (Telegram's auto-download threshold) at 720p,
    then 480p, but only while the bitrate stays above the quality floor.
    Tier 2 runs when the floor blocked tier 1 and the original is over
    *hard_limit* (the send cap): aim at the cap at 720p — a bigger file that
    still looks good beats a tiny blurry one. Returns the original when
    nothing improved on it; the caller drops what is still over the cap.
    """
    for max_height, floor_bps in _soft_passes():
        if max_height != 720:
            logger.info("compress_video_retry_480p", original_mb=_mb(len(data)))
        compressed = await compress_video(
            data, soft_limit, max_height=max_height, min_video_bps=floor_bps
        )
        if compressed and len(compressed) <= soft_limit:
            return compressed

    if len(data) <= hard_limit:
        logger.info(
            "compress_video_kept_original",
            original_mb=_mb(len(data)),
            reason="soft target would breach the quality floor",
        )
        return data

    logger.info("compress_video_retry_send_cap", original_mb=_mb(len(data)), cap_mb=_mb(hard_limit))
    compressed = await compress_video(data, hard_limit, max_height=720)
    if compressed and len(compressed) < len(data):
        return compressed
    return data


async def ensure_within_limit(
    items: list[MediaItem], limit_bytes: int, hard_limit_bytes: int | None = None
) -> list[MediaItem]:
    """Compress media items that exceed *limit_bytes*; drop what can't be sent.

    - Videos/animations are re-encoded via ffmpeg (see `_shrink_video`).
    - Images are optimized via Pillow.
    - If compression is unavailable or fails, the item is kept as-is — unless
      it is still over *hard_limit_bytes* (Telegram's send cap), in which case
      it is dropped so the API call doesn't fail with a 413 later.
    """
    if limit_bytes <= 0:
        return items
    hard_limit = hard_limit_bytes if hard_limit_bytes is not None else limit_bytes

    for item in items:
        if item.data is None or len(item.data) <= limit_bytes:
            continue

        original_mb = _mb(len(item.data))

        if item.media_type in (MediaType.VIDEO, MediaType.ANIMATION):
            if not _check_ffmpeg():
                continue
            item.data = await _shrink_video(item.data, limit_bytes, hard_limit)
            if len(item.data) > limit_bytes:
                # Expected outcome of tier 2 (quality floor won), not an error.
                logger.info(
                    "compress_video_still_over_limit",
                    original_mb=original_mb,
                    final_mb=_mb(len(item.data)),
                    limit_mb=_mb(limit_bytes),
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
                    final_mb=_mb(len(item.data)),
                )

    return _drop_unsendable(items, hard_limit)


def _drop_unsendable(items: list[MediaItem], hard_limit: int) -> list[MediaItem]:
    sendable: list[MediaItem] = []
    for item in items:
        if item.data is not None and len(item.data) > hard_limit:
            logger.warning(
                "media_too_large_to_send",
                url=item.url,
                size_mb=_mb(len(item.data)),
                cap_mb=_mb(hard_limit),
            )
            continue
        sendable.append(item)
    return sendable


def is_image(item: MediaItem) -> bool:
    return item.media_type == MediaType.IMAGE


def is_video(item: MediaItem) -> bool:
    return item.media_type == MediaType.VIDEO


def is_animation(item: MediaItem) -> bool:
    return item.media_type == MediaType.ANIMATION
