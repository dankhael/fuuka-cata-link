"""Benchmark tests for Twitter/X scraper extraction speed."""

from __future__ import annotations

import time

import pytest

from src.scrapers.twitter import TwitterScraper

from .benchmark_urls import TWITTER_URLS
from .conftest import download_and_save


def _get_parametrize_data() -> list[tuple[str, str]]:
    """Build (size_category, url) pairs from configured URLs."""
    pairs = []
    for size, urls in TWITTER_URLS.items():
        for url in urls:
            pairs.append((size, url))
    return pairs


_TEST_DATA = _get_parametrize_data()


@pytest.mark.benchmark
@pytest.mark.skipif(not _TEST_DATA, reason="No Twitter benchmark URLs configured")
@pytest.mark.parametrize(
    "size_category,url",
    _TEST_DATA,
    ids=[f"{s}-{i}" for i, (s, _) in enumerate(_TEST_DATA)],
)
async def test_twitter_extraction_speed(size_category: str, url: str, bench):
    """Benchmark Twitter scraper extraction for various content sizes."""
    scraper = TwitterScraper()

    start = time.perf_counter()
    try:
        result = await scraper.extract(url)
        extract_time = time.perf_counter() - start

        dl_time, total_bytes, saved = await download_and_save(result, "twitter", size_category)

        bench.record(
            platform="twitter",
            size_category=size_category,
            url=url,
            extraction_time_s=extract_time,
            download_time_s=dl_time,
            total_data_bytes=total_bytes,
            saved_files=saved,
            result=result,
        )

        assert result.method_used != "none", f"All extraction methods failed for {url}"
        assert result.has_media or result.caption, f"No media or caption extracted from {url}"

    except Exception as exc:
        elapsed = time.perf_counter() - start
        bench.record(
            platform="twitter",
            size_category=size_category,
            url=url,
            extraction_time_s=elapsed,
            error=str(exc),
        )
        pytest.fail(f"Twitter extraction failed for {url}: {exc}")
