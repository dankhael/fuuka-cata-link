from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.bot import handlers
from src.scrapers.base import MediaItem, MediaType, ScrapedMedia, SkipExtraction
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


async def _capture_handle(text: str):
    """Run handle_media_link with the given text and capture the flags
    forwarded to _process_links."""
    captured: dict = {}

    async def fake_process(
        msg, links, *, strip_referenced=False, strip_caption=False, translate=False
    ):
        captured["strip_caption"] = strip_caption
        captured["strip_referenced"] = strip_referenced
        captured["translate"] = translate
        captured["links"] = links

    message = AsyncMock()
    message.text = text
    links = [_link()]

    with patch.object(handlers, "_process_links", fake_process):
        await handlers.handle_media_link(message, links)

    return captured


async def test_handle_media_link_no_command_uses_defaults():
    captured = await _capture_handle("https://x.com/u/status/1")
    assert captured["strip_caption"] is False
    assert captured["strip_referenced"] is False


async def test_handle_media_link_nocaption_sets_strip_caption():
    captured = await _capture_handle("/nocaption https://x.com/u/status/1")
    assert captured["strip_caption"] is True
    assert captured["strip_referenced"] is False


async def test_handle_media_link_noreply_sets_strip_referenced():
    captured = await _capture_handle("/noreply https://x.com/u/status/1")
    assert captured["strip_caption"] is False
    assert captured["strip_referenced"] is True


async def test_handle_media_link_combines_noreply_and_nocaption():
    captured = await _capture_handle("/noreply /nocaption https://x.com/u/status/1")
    assert captured["strip_caption"] is True
    assert captured["strip_referenced"] is True


async def test_handle_media_link_command_order_does_not_matter():
    captured = await _capture_handle("https://x.com/u/status/1 /nocaption /noreply")
    assert captured["strip_caption"] is True
    assert captured["strip_referenced"] is True


async def test_handle_media_link_ignore_short_circuits():
    process = AsyncMock()
    message = AsyncMock()
    message.text = "/ignore /nocaption /noreply https://x.com/u/status/1"

    with patch.object(handlers, "_process_links", process):
        await handlers.handle_media_link(message, [_link()])

    process.assert_not_awaited()


async def test_handle_media_link_translate_sets_flag():
    captured = await _capture_handle("/translate https://x.com/u/status/1")
    assert captured["translate"] is True
    assert captured["strip_caption"] is False
    assert captured["strip_referenced"] is False


async def test_translate_replaces_caption_for_twitter():
    scraper = AsyncMock()
    result = _result(caption="hello world")
    result.lang = "en"
    scraper.extract.return_value = result
    message = AsyncMock()
    send = AsyncMock()
    fake_translate = AsyncMock(return_value="olá mundo")

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
        patch.object(handlers, "translate_to_portuguese", fake_translate),
    ):
        await handlers._process_links(message, [_link()], translate=True)

    fake_translate.assert_awaited_once_with("hello world", "en")
    sent = send.call_args.args[1]
    assert sent.caption == "olá mundo"


async def test_translate_skipped_for_non_twitter():
    scraper = AsyncMock()
    yt_result = ScrapedMedia(
        platform=Platform.YOUTUBE,
        original_url="https://youtu.be/abc",
        caption="some title",
        media_items=[MediaItem(url="https://x/v.mp4", media_type=MediaType.VIDEO)],
    )
    yt_result.lang = "en"
    scraper.extract.return_value = yt_result
    message = AsyncMock()
    send = AsyncMock()
    fake_translate = AsyncMock(return_value="should not run")

    yt_link = DetectedLink(url="https://youtu.be/abc", platform=Platform.YOUTUBE, is_spoiler=False)
    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.YOUTUBE: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
        patch.object(handlers, "translate_to_portuguese", fake_translate),
    ):
        await handlers._process_links(message, [yt_link], translate=True)

    fake_translate.assert_not_awaited()
    sent = send.call_args.args[1]
    assert sent.caption == "some title"


async def test_translate_handles_main_and_referenced_independently():
    scraper = AsyncMock()
    result = _result(caption="english main", with_ref=True, ref_caption="japanese parent")
    result.lang = "en"
    result.referenced_post.lang = "ja"
    scraper.extract.return_value = result
    message = AsyncMock()
    send = AsyncMock()

    async def translate(text: str, lang: str | None) -> str:
        return f"pt({lang}):{text}"

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
        patch.object(handlers, "translate_to_portuguese", side_effect=translate),
    ):
        await handlers._process_links(message, [_link()], translate=True)

    sent = send.call_args.args[1]
    assert sent.caption == "pt(en):english main"
    assert sent.referenced_post.caption == "pt(ja):japanese parent"


async def test_translate_skipped_when_nocaption_active():
    """``/nocaption`` strips captions before we'd translate them — translator
    must not run at all in that case (no API call, no cost)."""
    scraper = AsyncMock()
    result = _result(caption="english main")
    result.lang = "en"
    scraper.extract.return_value = result
    message = AsyncMock()
    send = AsyncMock()
    fake_translate = AsyncMock(return_value="should not run")

    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.TWITTER: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
        patch.object(handlers, "translate_to_portuguese", fake_translate),
    ):
        await handlers._process_links(message, [_link()], strip_caption=True, translate=True)

    fake_translate.assert_not_awaited()


async def test_skip_extraction_results_in_no_reply():
    """When a scraper raises ``SkipExtraction`` (e.g. YouTube video over the
    duration cap), the handler must stay completely silent — no reply, no
    error message, no send."""
    scraper = AsyncMock()
    scraper.extract.side_effect = SkipExtraction("video too long")
    message = AsyncMock()
    send = AsyncMock()

    yt_link = DetectedLink(url="https://youtu.be/abc", platform=Platform.YOUTUBE, is_spoiler=False)
    with (
        patch.dict(handlers._SCRAPER_MAP, {Platform.YOUTUBE: scraper}, clear=True),
        patch.object(handlers, "_send_result", send),
    ):
        await handlers._process_links(message, [yt_link])

    send.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.answer.assert_not_awaited()
