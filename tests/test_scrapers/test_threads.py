import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scrapers import SCRAPERS
from src.scrapers.base import MediaType
from src.scrapers.threads import ThreadsScraper
from src.utils.link_detector import Platform, detect_links

URL = "https://www.threads.com/@alice/post/ABC_123"


async def extract_html(html: str, url: str = URL):
    response = AsyncMock()
    response.raise_for_status = MagicMock()
    response.text.return_value = html
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = response
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    with patch("src.scrapers.threads.aiohttp.ClientSession", return_value=session):
        return await ThreadsScraper().extract(url)


def post_html(post: dict) -> str:
    payload = {
        "require": [
            [
                "ScheduledServerJS",
                {
                    "data": {
                        "thread_items": [
                            {
                                "post": {
                                    "code": "OTHER",
                                    "user": {"username": "other"},
                                    "caption": {"text": "Wrong post"},
                                }
                            },
                            {"post": post},
                        ]
                    }
                },
            ]
        ]
    }
    return '<script type="application/json">' + json.dumps(payload) + "</script>"


@pytest.mark.parametrize("domain", ["threads.net", "threads.com", "www.threads.com"])
@pytest.mark.parametrize("path", ["@alice/post/ABC_123", "t/ABC_123"])
def test_detect_threads(domain, path):
    url = f"https://{domain}/{path}"
    links = detect_links(f"{url}?xmt=share {url}")
    assert len(links) == 1
    assert links[0].platform == Platform.THREADS
    assert links[0].url == url


@pytest.mark.parametrize(
    "url",
    [
        "https://threads.com/@alice",
        "https://threads.com/search?q=hello",
        "https://threads.com.evil.test/@alice/post/ABC_123",
    ],
)
def test_ignore_non_posts(url):
    assert detect_links(url) == []


def test_registered():
    assert ThreadsScraper in SCRAPERS


async def test_text_post_escapes_telegram_html():
    result = await extract_html(
        post_html(
            {
                "code": "ABC_123",
                "user": {"username": "alice"},
                "caption": {"text": "Hello <world> & friends"},
            }
        )
    )
    assert result.platform == Platform.THREADS
    assert result.author == "alice"
    assert result.caption == "Hello &lt;world&gt; &amp; friends"
    assert result.original_url == URL
    assert result.media_items == []


async def test_mixed_carousel_chooses_best_media_without_video_thumbnail():
    result = await extract_html(
        post_html(
            {
                "code": "ABC_123",
                "user": {"username": "alice"},
                "carousel_media": [
                    {
                        "image_versions2": {
                            "candidates": [
                                {"url": "https://cdn.test/small.jpg", "width": 100, "height": 100},
                                {
                                    "url": "https://cdn.test/large.jpg",
                                    "width": 1000,
                                    "height": 1000,
                                },
                            ]
                        }
                    },
                    {
                        "video_versions": [{"url": "https://cdn.test/video.mp4"}],
                        "image_versions2": {"candidates": [{"url": "https://cdn.test/thumb.jpg"}]},
                    },
                ],
            }
        )
    )
    assert [item.url for item in result.media_items] == [
        "https://cdn.test/large.jpg",
        "https://cdn.test/video.mp4",
    ]
    assert [item.media_type for item in result.media_items] == [MediaType.IMAGE, MediaType.VIDEO]


async def test_single_image_null_fields():
    result = await extract_html(
        post_html(
            {
                "code": "ABC_123",
                "user": {"username": "alice"},
                "caption": None,
                "carousel_media": None,
                "video_versions": None,
                "image_versions2": {"candidates": [{"url": "https://cdn.test/photo.jpg?a=1&b=2"}]},
            }
        )
    )
    assert result.media_items[0].url == "https://cdn.test/photo.jpg?a=1&b=2"


async def test_opengraph_fallback_attribute_order_and_entities():
    result = await extract_html(
        '<script type="application/json">invalid</script>'
        f'<meta content="{URL}" property="og:url">'
        '<meta content="A &quot;post&quot; &amp; more" property="og:description">'
        '<meta property="og:video" content="https://cdn.test/video.mp4?a=1&amp;b=2">'
        '<meta property="og:image" content="https://cdn.test/thumb.jpg">'
    )
    assert result.caption == "A &quot;post&quot; &amp; more"
    assert result.media_items[0].url == "https://cdn.test/video.mp4?a=1&b=2"
    assert result.media_items[0].media_type == MediaType.VIDEO


@pytest.mark.parametrize(
    "html",
    [
        '<meta property="og:image" content="https://cdn.test/logo.png">',
        '<meta property="og:url" content="https://threads.com/@alice/post/OTHER">'
        '<meta property="og:description" content="Another post">',
        post_html({"code": "ABC_123", "user": {"username": "alice"}, "caption": None}),
    ],
)
async def test_unavailable_or_unrelated_post_fails(html):
    with pytest.raises(RuntimeError, match="all extraction methods failed"):
        await extract_html(html)


async def test_http_failure():
    with patch("src.scrapers.threads.aiohttp.ClientSession") as session:
        session.return_value.__aenter__ = AsyncMock(side_effect=TimeoutError)
        with pytest.raises(RuntimeError, match="all extraction methods failed"):
            await ThreadsScraper().extract(URL)
