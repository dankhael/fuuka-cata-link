import pytest

from src.scrapers.base import BaseScraper, MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import Platform


class DummyScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TWITTER

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            caption="primary",
            media_items=[MediaItem(url="http://img.jpg", media_type=MediaType.IMAGE)],
        )


class FailingPrimaryScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TWITTER

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        raise RuntimeError("primary failed")

    async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
        return ScrapedMedia(
            platform=self.platform,
            original_url=url,
            caption="ytdlp fallback",
            media_items=[MediaItem(url="http://vid.mp4", media_type=MediaType.VIDEO)],
        )


class AllFailScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TWITTER

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        raise RuntimeError("fail")


@pytest.mark.asyncio
async def test_primary_extraction_succeeds():
    scraper = DummyScraper()
    result = await scraper.extract("https://example.com")
    assert result.caption == "primary"
    assert result.method_used == "primary"


@pytest.mark.asyncio
async def test_fallback_to_ytdlp():
    scraper = FailingPrimaryScraper()
    result = await scraper.extract("https://example.com")
    assert result.caption == "ytdlp fallback"
    assert result.method_used == "yt-dlp"


@pytest.mark.asyncio
async def test_all_methods_fail():
    scraper = AllFailScraper()
    result = await scraper.extract("https://example.com")
    assert result.method_used == "none"
    assert "Could not extract" in result.caption


@pytest.mark.asyncio
async def test_has_media_property():
    with_media = ScrapedMedia(
        platform=Platform.TWITTER,
        original_url="https://example.com",
        media_items=[MediaItem(url="http://img.jpg", media_type=MediaType.IMAGE)],
    )
    without_media = ScrapedMedia(
        platform=Platform.TWITTER,
        original_url="https://example.com",
    )
    assert with_media.has_media is True
    assert without_media.has_media is False


@pytest.mark.asyncio
async def test_pre_populated_data_preserved():
    """Test that items with pre-populated data pass through the fallback chain."""
    class PrePopulatedScraper(BaseScraper):
        @property
        def platform(self) -> Platform:
            return Platform.TIKTOK

        async def _primary_extract(self, url: str) -> ScrapedMedia:
            item = MediaItem(url=url, media_type=MediaType.VIDEO)
            item.data = b"pre_downloaded"
            return ScrapedMedia(
                platform=self.platform,
                original_url=url,
                media_items=[item],
            )

    scraper = PrePopulatedScraper()
    result = await scraper.extract("https://tiktok.com/video")
    assert result.media_items[0].data == b"pre_downloaded"
