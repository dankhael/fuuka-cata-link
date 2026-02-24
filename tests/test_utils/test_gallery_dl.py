import json
from unittest.mock import AsyncMock, patch

import pytest

from src.utils.gallery_dl import GalleryDlResult, gallery_dl_download


def _make_process(returncode: int, stdout: bytes = b"", stderr: bytes = b""):
    """Create a mock subprocess result."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


@pytest.mark.asyncio
async def test_gallery_dl_download_success(tmp_path):
    """Successful download returns files and metadata."""
    # Prepare fake files in temp dir
    image_data = b"x" * 2048  # > 1KB threshold

    async def fake_subprocess(*cmd, **kwargs):
        # Write files to the dest directory (second arg after --dest)
        dest = cmd[2]  # gallery-dl --dest <dest> ...
        img = tmp_path / "test_image.jpg"
        img.write_bytes(image_data)
        meta = tmp_path / "test_image.json"
        meta.write_text(json.dumps({
            "description": "Test post",
            "username": "testuser",
        }))
        # Copy files to the actual dest used by gallery-dl
        from pathlib import Path
        dest_path = Path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "test_image.jpg").write_bytes(image_data)
        (dest_path / "test_image.json").write_text(meta.read_text())

        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await gallery_dl_download("https://instagram.com/p/ABC123/")

    assert isinstance(result, GalleryDlResult)
    assert len(result.files) == 1
    assert result.files[0].data == image_data
    assert result.files[0].ext == "jpg"
    assert result.files[0].is_video is False
    assert result.uploader == "testuser"
    assert result.description == "Test post"


@pytest.mark.asyncio
async def test_gallery_dl_download_failure():
    """Non-zero exit code raises RuntimeError."""
    proc = _make_process(1, stderr=b"ERROR: Unsupported URL")

    with patch(
        "src.utils.gallery_dl.asyncio.create_subprocess_exec",
        return_value=proc,
    ):
        with pytest.raises(RuntimeError, match="gallery-dl download failed"):
            await gallery_dl_download("https://instagram.com/p/BAD/")


@pytest.mark.asyncio
async def test_gallery_dl_no_files():
    """Empty download directory raises RuntimeError."""

    async def fake_subprocess(*cmd, **kwargs):
        # Don't write any files — simulates gallery-dl finding nothing
        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        with pytest.raises(RuntimeError, match="no usable media"):
            await gallery_dl_download("https://instagram.com/p/EMPTY/")


@pytest.mark.asyncio
async def test_gallery_dl_skips_tiny_files():
    """Files smaller than 1KB are skipped."""

    async def fake_subprocess(*cmd, **kwargs):
        from pathlib import Path
        dest_path = Path(cmd[2])
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "tiny.jpg").write_bytes(b"x" * 500)  # < 1KB

        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        with pytest.raises(RuntimeError, match="no usable media"):
            await gallery_dl_download("https://instagram.com/p/TINY/")


@pytest.mark.asyncio
async def test_gallery_dl_video_detection():
    """MP4 files are detected as video."""

    async def fake_subprocess(*cmd, **kwargs):
        from pathlib import Path
        dest_path = Path(cmd[2])
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "clip.mp4").write_bytes(b"x" * 5000)

        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await gallery_dl_download("https://instagram.com/reel/VID/")

    assert len(result.files) == 1
    assert result.files[0].is_video is True
    assert result.files[0].ext == "mp4"


@pytest.mark.asyncio
async def test_gallery_dl_passes_cookies():
    """Cookies file is passed to gallery-dl when provided."""
    captured_cmd = []

    async def fake_subprocess(*cmd, **kwargs):
        captured_cmd.extend(cmd)
        from pathlib import Path
        dest_path = Path(cmd[2])
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "img.jpg").write_bytes(b"x" * 2000)

        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await gallery_dl_download(
            "https://instagram.com/p/ABC/",
            cookies_file="/path/to/cookies.txt",
        )

    assert "--cookies" in captured_cmd
    cookies_idx = captured_cmd.index("--cookies")
    assert captured_cmd[cookies_idx + 1] == "/path/to/cookies.txt"


@pytest.mark.asyncio
async def test_gallery_dl_multiple_files():
    """Multiple files (carousel) are all returned."""

    async def fake_subprocess(*cmd, **kwargs):
        from pathlib import Path
        dest_path = Path(cmd[2])
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "img1.jpg").write_bytes(b"a" * 2000)
        (dest_path / "img2.png").write_bytes(b"b" * 3000)
        (dest_path / "img3.webp").write_bytes(b"c" * 4000)

        return _make_process(0)

    with patch("src.utils.gallery_dl.asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await gallery_dl_download("https://instagram.com/p/CAROUSEL/")

    assert len(result.files) == 3
    assert all(not f.is_video for f in result.files)
