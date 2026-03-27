"""Benchmark test infrastructure.

Provides timing fixtures and result collection for integration benchmarks
that hit real services. Results are written to a human-readable text file,
and downloaded media is saved to benchmarks_output/downloads/.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import pytest

from src.scrapers.base import MediaType, ScrapedMedia
from src.utils.media_handler import download_media

# Where downloaded media files are saved
DOWNLOADS_DIR = Path("benchmarks_output/downloads")

_EXT_MAP = {
    MediaType.IMAGE: ".jpg",
    MediaType.VIDEO: ".mp4",
    MediaType.ANIMATION: ".gif",
    MediaType.TEXT: ".txt",
    MediaType.CODE: ".txt",
}


@dataclass
class BenchmarkResult:
    """A single benchmark measurement."""

    platform: str
    size_category: str  # "small", "medium", "large"
    url: str
    extraction_time_s: float
    download_time_s: float
    method_used: str  # "primary", "yt-dlp", "browser", "none"
    media_count: int
    total_data_bytes: int
    saved_files: list[str]
    success: bool
    error: str | None = None


@dataclass
class BenchmarkCollector:
    """Accumulates benchmark results across the entire test session."""

    results: list[BenchmarkResult] = field(default_factory=list)
    session_start: float = field(default_factory=time.monotonic)

    def add(self, result: BenchmarkResult) -> None:
        self.results.append(result)

    def format_report(self) -> str:
        """Generate a human-readable report string."""
        session_duration = time.monotonic() - self.session_start
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines: list[str] = []
        lines.append("=" * 100)
        lines.append(f"BENCHMARK RESULTS -- {now}")
        lines.append(f"Total session time: {session_duration:.1f}s")
        lines.append(f"Total benchmarks run: {len(self.results)}")
        lines.append("=" * 100)
        lines.append("")

        # Group by platform
        platforms: dict[str, list[BenchmarkResult]] = {}
        for r in self.results:
            platforms.setdefault(r.platform, []).append(r)

        for platform, results in sorted(platforms.items()):
            lines.append(f"--- {platform.upper()} ---")
            lines.append(
                f"  {'Size':<8} {'Extract':<10} {'Download':<10} {'Total':<10} "
                f"{'Method':<10} {'Media':<6} {'Data Size':<12} {'Status':<8} URL"
            )
            lines.append(f"  {'-' * 92}")

            for r in results:
                data_str = _format_bytes(r.total_data_bytes) if r.success else "N/A"
                status = "OK" if r.success else "FAIL"
                total_time = r.extraction_time_s + r.download_time_s
                display_url = r.url if len(r.url) <= 50 else r.url[:47] + "..."
                lines.append(
                    f"  {r.size_category:<8} {r.extraction_time_s:<10.3f} "
                    f"{r.download_time_s:<10.3f} {total_time:<10.3f} "
                    f"{r.method_used:<10} {r.media_count:<6} {data_str:<12} "
                    f"{status:<8} {display_url}"
                )
                if r.saved_files:
                    for f in r.saved_files:
                        lines.append(f"           -> {f}")
                if r.error:
                    lines.append(f"           Error: {r.error}")

            lines.append("")

        # Summary statistics
        if self.results:
            lines.append("--- SUMMARY ---")
            successful = [r for r in self.results if r.success]
            failed = [r for r in self.results if not r.success]
            lines.append(f"  Successful: {len(successful)}/{len(self.results)}")
            if successful:
                avg_extract = sum(r.extraction_time_s for r in successful) / len(successful)
                avg_download = sum(r.download_time_s for r in successful) / len(successful)
                total_bytes = sum(r.total_data_bytes for r in successful)
                total_files = sum(len(r.saved_files) for r in successful)
                lines.append(f"  Average extraction time: {avg_extract:.3f}s")
                lines.append(f"  Average download time:   {avg_download:.3f}s")
                lines.append(f"  Total data downloaded:   {_format_bytes(total_bytes)}")
                lines.append(f"  Total files saved:       {total_files}")
            if failed:
                lines.append("  Failed URLs:")
                for r in failed:
                    lines.append(f"    - [{r.platform}] {r.url}: {r.error}")
            lines.append("")

        lines.append("=" * 100)
        return "\n".join(lines)


def _format_bytes(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Download + save helper
# ---------------------------------------------------------------------------


async def download_and_save(
    result: ScrapedMedia,
    platform: str,
    size_category: str,
) -> tuple[float, int, list[str]]:
    """Download any media items missing data, then save all to disk.

    Returns (download_time_seconds, total_bytes, list_of_saved_file_paths).
    """
    items_needing_download = [item for item in result.media_items if item.data is None]

    # Download items that only have URLs
    dl_start = time.perf_counter()
    if items_needing_download:
        async with aiohttp.ClientSession() as session:
            await download_media(items_needing_download, session)
    download_time = time.perf_counter() - dl_start

    # Calculate total bytes across all items
    total_bytes = sum(len(item.data) for item in result.media_items if item.data)

    # Save files to disk
    save_dir = DOWNLOADS_DIR / platform / size_category
    save_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[str] = []
    for i, item in enumerate(result.media_items):
        if item.data is None:
            continue
        ext = _EXT_MAP.get(item.media_type, ".bin")
        filename = f"{platform}_{size_category}_{i}{ext}"
        filepath = save_dir / filename
        filepath.write_bytes(item.data)
        saved_files.append(str(filepath))

    return download_time, total_bytes, saved_files


# ---------------------------------------------------------------------------
# Session-scoped collector
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def benchmark_collector():
    """Collect all benchmark results across the session."""
    return BenchmarkCollector()


@pytest.fixture(scope="session", autouse=True)
def _write_benchmark_report(benchmark_collector: BenchmarkCollector):
    """Write the benchmark report to a file after all tests complete."""
    yield  # Let all tests run

    if not benchmark_collector.results:
        return

    report = benchmark_collector.format_report()

    # Print to console
    print("\n\n" + report)

    # Write to file
    output_dir = Path("benchmarks_output")
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"benchmark_{timestamp}.txt"
    output_file.write_text(report, encoding="utf-8")

    # Also write a "latest" copy for quick access
    latest_file = output_dir / "benchmark_latest.txt"
    latest_file.write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-test benchmark fixture
# ---------------------------------------------------------------------------


class _BenchmarkRecorder:
    """Helper object returned by the `bench` fixture."""

    def __init__(self, collector: BenchmarkCollector):
        self._collector = collector

    def record(
        self,
        platform: str,
        size_category: str,
        url: str,
        extraction_time_s: float,
        download_time_s: float = 0.0,
        total_data_bytes: int = 0,
        saved_files: list[str] | None = None,
        result=None,
        error: str | None = None,
    ) -> None:
        if result is not None and result.method_used != "none":
            self._collector.add(
                BenchmarkResult(
                    platform=platform,
                    size_category=size_category,
                    url=url,
                    extraction_time_s=extraction_time_s,
                    download_time_s=download_time_s,
                    method_used=result.method_used,
                    media_count=len(result.media_items),
                    total_data_bytes=total_data_bytes,
                    saved_files=saved_files or [],
                    success=True,
                )
            )
        else:
            self._collector.add(
                BenchmarkResult(
                    platform=platform,
                    size_category=size_category,
                    url=url,
                    extraction_time_s=extraction_time_s,
                    download_time_s=download_time_s,
                    method_used=result.method_used if result else "none",
                    media_count=0,
                    total_data_bytes=0,
                    saved_files=[],
                    success=False,
                    error=error or (result.caption if result else "Unknown error"),
                )
            )


@pytest.fixture
def bench(benchmark_collector: BenchmarkCollector):
    """Fixture providing a recorder to capture benchmark results.

    Usage in test::

        start = time.perf_counter()
        result = await scraper.extract(url)
        extract_time = time.perf_counter() - start

        dl_time, total_bytes, saved = await download_and_save(result, "twitter", "small")

        bench.record(
            platform="twitter",
            size_category="small",
            url=url,
            extraction_time_s=extract_time,
            download_time_s=dl_time,
            total_data_bytes=total_bytes,
            saved_files=saved,
            result=result,
        )
    """
    return _BenchmarkRecorder(benchmark_collector)
