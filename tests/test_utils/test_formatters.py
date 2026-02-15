from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.utils.formatters import format_caption, format_text_post, truncate
from src.utils.link_detector import Platform


class TestFormatCaption:
    def test_media_with_author_and_caption(self):
        result = ScrapedMedia(
            platform=Platform.TWITTER,
            original_url="https://twitter.com/user/status/1",
            author="testuser",
            caption="Hello world",
            media_items=[MediaItem(url="http://img.jpg", media_type=MediaType.IMAGE)],
        )
        caption = format_caption(result)
        assert "testuser" in caption
        assert "Hello world" in caption
        # Should use HTML hyperlink, not plain URL
        assert '<a href="https://twitter.com/user/status/1">Link</a>' in caption

    def test_media_without_author(self):
        result = ScrapedMedia(
            platform=Platform.INSTAGRAM,
            original_url="https://instagram.com/p/123",
            caption="Nice photo",
            media_items=[MediaItem(url="http://img.jpg", media_type=MediaType.IMAGE)],
        )
        caption = format_caption(result)
        assert "Nice photo" in caption
        assert '<a href="https://instagram.com/p/123">Link</a>' in caption

    def test_no_media_returns_url(self):
        result = ScrapedMedia(
            platform=Platform.TWITTER,
            original_url="https://twitter.com/user/status/1",
        )
        caption = format_caption(result)
        assert caption == "https://twitter.com/user/status/1"

    def test_hyperlink_not_plain_text(self):
        result = ScrapedMedia(
            platform=Platform.TIKTOK,
            original_url="https://tiktok.com/@user/video/123",
            caption="Cool video",
            media_items=[MediaItem(url="http://vid.mp4", media_type=MediaType.VIDEO)],
        )
        caption = format_caption(result)
        # Should NOT contain plain "Source:" format
        assert "Source:" not in caption
        assert "Link</a>" in caption


class TestFormatTextPost:
    def test_text_post_with_content(self):
        result = ScrapedMedia(
            platform=Platform.REDDIT,
            original_url="https://reddit.com/r/test/...",
            author="u/someone",
            caption="Long text post here",
        )
        text = format_text_post(result)
        assert "u/someone" in text
        assert "Long text post here" in text
        assert "reddit.com" not in text  # no source link for text posts

    def test_text_post_no_content(self):
        result = ScrapedMedia(
            platform=Platform.REDDIT,
            original_url="https://reddit.com/r/test/...",
        )
        assert format_text_post(result) == "(no content)"


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        result = truncate("a" * 200, 50)
        assert len(result) == 50
        assert result.endswith("...")

    def test_exact_length_unchanged(self):
        text = "a" * 100
        assert truncate(text, 100) == text
