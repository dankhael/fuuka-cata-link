from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.scrapers.base import ScrapedMedia


@dataclass
class CacheEntry:
    result: ScrapedMedia
    created_at: float = field(default_factory=time.monotonic)


class MediaCache:
    """Simple in-memory TTL cache for scraped results."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 200) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, CacheEntry] = {}

    def get(self, url: str) -> ScrapedMedia | None:
        entry = self._store.get(url)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at > self._ttl:
            del self._store[url]
            return None
        return entry.result

    def put(self, url: str, result: ScrapedMedia) -> None:
        self._evict()
        self._store[url] = CacheEntry(result=result)

    def _evict(self) -> None:
        """Remove expired entries and trim to max size."""
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now - v.created_at > self._ttl]
        for k in expired:
            del self._store[k]
        while len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
            del self._store[oldest_key]
