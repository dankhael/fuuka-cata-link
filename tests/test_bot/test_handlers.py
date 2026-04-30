from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.bot import handlers
from src.scrapers.base import MediaItem, MediaType, ScrapedMedia
from src.utils.link_detector import DetectedLink, Platform


def _link(url: str = "https://x.com/u/status/1") -> DetectedLink:
    return DetectedLink(url=url, platform=Platform.TWITTER, is_spoiler=False)


def _result(
    *,
    caption: str | None = "hello",
    with_media: bool = True,
    with_ref: bool = False,
    ref_caption: str | None = "parent caption",
) -> ScrapedMedia:
    items = (
        [MediaItem(url="https://example.com/i.jpg", media_type=MediaType.IMAGE)]
        if with_media
        else []
    )
    ref = None
    if with_ref:
        ref = ScrapedMedia(
            platform=Platform.TWITTER,
            original_url="https://x.com/u/status/0",
            caption=ref_caption,
            media_items=[MediaItem(url="https://example.com/p.jpg", media_type=MediaType.IMAGE)],
        )
    return ScrapedMedia(
        platform=Platform.TWITTER,
        original_url="https://x.com/u/status/1",
        caption=caption,
        media_items=items,
        referenced_post=ref,
        reference_type="quote" if with_ref else None,
    )


async def test_strip_caption_drops_caption_and_keeps_media():
    scraper = AsyncMock()
    scraper.extract.return_value = _result(caption="should be dropped")
    message = AsyncMock()
    send = AsyncMock()

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
    ):
        await handlers._process_links(message, [_link()], strip_caption=True)

    assert send.await_count == 1
    sent = send.call_args.args[1]
    assert sent.caption is None
    assert sent.has_media


async def test_strip_caption_skips_text_only_post():
    scraper = AsyncMock()
    scraper.extract.return_value = _result(caption="just words", with_media=False)
    message = AsyncMock()
    send = AsyncMock()

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
    ):
        await handlers._process_links(message, [_link()], strip_caption=True)

    send.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.answer.assert_not_awaited()


async def test_strip_caption_also_strips_referenced_post_caption():
    scraper = AsyncMock()
    scraper.extract.return_value = _result(with_ref=True)
    message = AsyncMock()
    send = AsyncMock()

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
    ):
        await handlers._process_links(message, [_link()], strip_caption=True)

    sent = send.call_args.args[1]
    assert sent.caption is None
    assert sent.referenced_post is not None
    assert sent.referenced_post.caption is None


async def test_default_mode_keeps_caption():
    scraper = AsyncMock()
    scraper.extract.return_value = _result(caption="keep me")
    message = AsyncMock()
    send = AsyncMock()

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
    ):
        await handlers._process_links(message, [_link()])

    sent = send.call_args.args[1]
    assert sent.caption == "keep me"


def test_wrap_spoiler_wraps_when_flag_set():
    assert handlers._wrap_spoiler("hello", True) == "<tg-spoiler>hello</tg-spoiler>"


def test_wrap_spoiler_passthrough_when_flag_unset():
    assert handlers._wrap_spoiler("hello", False) == "hello"


def test_wrap_spoiler_empty_text_unchanged():
    assert handlers._wrap_spoiler("", True) == ""


async def test_send_single_result_spoilers_text_post():
    message = AsyncMock()
    result = _result(caption="secret", with_media=False)

    await handlers._send_single_result(message, result, has_spoiler=True)

    message.reply.assert_awaited_once()
    sent_text = message.reply.await_args.args[0]
    assert sent_text.startswith("<tg-spoiler>")
    assert sent_text.endswith("</tg-spoiler>")
    assert "secret" in sent_text


async def test_send_single_result_text_post_no_spoiler_unchanged():
    message = AsyncMock()
    result = _result(caption="public", with_media=False)

    await handlers._send_single_result(message, result, has_spoiler=False)

    sent_text = message.reply.await_args.args[0]
    assert "<tg-spoiler>" not in sent_text


async def test_handle_nocaption_dispatches_with_strip_caption():
    captured: dict = {}

    async def fake_process(msg, links, *, strip_referenced=False, strip_caption=False):
        captured["strip_caption"] = strip_caption
        captured["strip_referenced"] = strip_referenced
        captured["links"] = links

    message = AsyncMock()
    links = [_link()]

    with patch.object(handlers, "_process_links", fake_process):
        await handlers.handle_nocaption(message, links)

    assert captured == {
        "strip_caption": True,
        "strip_referenced": False,
        "links": links,
    }
