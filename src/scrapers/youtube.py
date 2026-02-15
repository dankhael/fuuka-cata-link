from __future__ import annotations

import asyncio
import json

from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform


class YouTubeScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        """Use yt-dlp as the primary method for YouTube (most reliable)."""
        return await self._ytdlp_extract(url)

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--dump-json",
            "--no-download",
            "-f", "best[filesize<50M]/best",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {stderr.decode()}")

        data = json.loads(stdout)

        # Pick best format URL
        video_url = data.get("url", "")
        if not video_url and data.get("formats"):
            video_url = data["formats"][-1].get("url", "")

        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            author=data.get("uploader"),
            caption=data.get("title"),
            media_items=[MediaItem(url=video_url, media_type=MediaType.VIDEO)],
        )
