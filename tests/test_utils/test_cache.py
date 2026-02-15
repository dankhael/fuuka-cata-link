import time
from unittest.mock import patch

from src.scrapers.base import ScrapedMedia
from src.utils.cache import MediaCache
from src.utils.link_detector import Platform


def _make_result(url: str) -> ScrapedMedia:
    return ScrapedMedia(platform=Platform.TWITTER, original_url=url, caption="test")


class TestMediaCache:
    def test_put_and_get(self):
        cache = MediaCache(ttl_seconds=60)
        result = _make_result("https://example.com/1")
        cache.put("https://example.com/1", result)
        assert cache.get("https://example.com/1") is result

    def test_miss(self):
        cache = MediaCache()
        assert cache.get("https://nonexistent.com") is None

    def test_ttl_expiry(self):
        cache = MediaCache(ttl_seconds=1)
        result = _make_result("https://example.com/1")
        cache.put("https://example.com/1", result)

        # Simulate time passing
        with patch("src.utils.cache.time.monotonic", return_value=time.monotonic() + 2):
            assert cache.get("https://example.com/1") is None

    def test_max_size_eviction(self):
        cache = MediaCache(ttl_seconds=60, max_size=3)
        for i in range(5):
            cache.put(f"url_{i}", _make_result(f"url_{i}"))
        # Should not exceed max_size
        assert len(cache._store) <= 3
