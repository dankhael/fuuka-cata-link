import pytest
from structlog.testing import capture_logs

from src.scrapers.base import (
    BaseScraper,
    MediaItem,
    MediaType,
    ScrapedMedia,
    SkipExtraction,
    describe_exception,
)
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
    with pytest.raises(RuntimeError, match="all extraction methods failed"):
        await scraper.extract("https://example.com")


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
async def test_skip_extraction_bypasses_fallbacks():
    """``SkipExtraction`` must propagate out of ``extract`` instead of being
    swallowed as a failed method and triggering the next fallback."""
    ytdlp_called = False

    class SkippingScraper(BaseScraper):
        @property
        def platform(self) -> Platform:
            return Platform.YOUTUBE

        async def _primary_extract(self, url: str) -> ScrapedMedia:
            raise SkipExtraction("over the cap")

        async def _ytdlp_extract(self, url: str) -> ScrapedMedia:
            nonlocal ytdlp_called
            ytdlp_called = True
            return ScrapedMedia(platform=self.platform, original_url=url)

    scraper = SkippingScraper()
    with pytest.raises(SkipExtraction):
        await scraper.extract("https://youtu.be/x")
    assert ytdlp_called is False


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


class TimingOutScraper(BaseScraper):
    @property
    def platform(self) -> Platform:
        return Platform.TIKTOK

    async def _primary_extract(self, url: str) -> ScrapedMedia:
        raise TimeoutError()


def test_describe_exception_falls_back_to_the_class_name():
    assert describe_exception(TimeoutError()) == "TimeoutError"
    assert describe_exception(RuntimeError("boom")) == "boom"


@pytest.mark.asyncio
async def test_empty_exception_message_is_logged_by_type():
    """Regression: a tikwm timeout was logged as error="" — nothing to grep for."""
    with capture_logs() as logs:
        with pytest.raises(RuntimeError):
            await TimingOutScraper().extract("https://vt.tiktok.com/x/")

    failures = [entry for entry in logs if entry["event"] == "extraction_method_failed"]
    assert failures[0]["error"] == "TimeoutError"
