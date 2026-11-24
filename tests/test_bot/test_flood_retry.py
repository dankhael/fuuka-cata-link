from __future__ import annotations

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates, SendMessage, SendVideo
from aiogram.types import BufferedInputFile
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.bot.flood_retry import FloodRetryMiddleware
from src.main import build_bot

SEND = SendMessage(chat_id=-100, text="oi")


def _flood(retry_after: int = 8, method=SEND) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=method, message="Too Many Requests", retry_after=retry_after)


class FakeSleep:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class FakeRequest:
    """Next middleware in the chain: raises each error in turn, then succeeds."""

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls = 0

    async def __call__(self, bot, method) -> str:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return "sent"


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


async def test_flood_control_waits_then_resends(sleep):
    """Regression: a 429 on SendVideo ("Retry in 8 seconds") lost the video."""
    request = FakeRequest(_flood(8))

    result = await FloodRetryMiddleware(sleep=sleep)(request, None, SEND)

    assert result == "sent"
    assert sleep.waits == [8]
    assert request.calls == 2


async def test_flood_control_gives_up_after_max_retries(sleep):
    request = FakeRequest(*[_flood(1) for _ in range(5)])

    with pytest.raises(TelegramRetryAfter):
        await FloodRetryMiddleware(max_retries=3, sleep=sleep)(request, None, SEND)

    assert request.calls == 4  # first try + 3 retries
    assert sleep.waits == [1, 1, 1]


async def test_flood_control_does_not_wait_past_the_ceiling(sleep):
    request = FakeRequest(_flood(61))

    with pytest.raises(TelegramRetryAfter):
        await FloodRetryMiddleware(max_wait_seconds=60, sleep=sleep)(request, None, SEND)

    assert request.calls == 1
    assert sleep.waits == []


async def test_network_timeout_is_not_retried(sleep):
    """The message may have been delivered; retrying could post it twice."""
    request = FakeRequest(TelegramNetworkError(method=SEND, message="Request timeout error"))

    with pytest.raises(TelegramNetworkError):
        await FloodRetryMiddleware(sleep=sleep)(request, None, SEND)

    assert request.calls == 1


async def test_polling_keeps_its_own_backoff(sleep):
    poll = GetUpdates()
    request = FakeRequest(_flood(5, poll))

    with pytest.raises(TelegramRetryAfter):
        await FloodRetryMiddleware(sleep=sleep)(request, None, poll)

    assert sleep.waits == []


class FakeTelegram:
    """Local Bot API: answers the first upload with 429, then accepts it."""

    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    async def send_video(self, request: web.Request) -> web.Response:
        form = await request.post()
        attached = next(part for part in form.values() if isinstance(part, web.FileField))
        self.uploads.append(attached.file.read())
        if len(self.uploads) == 1:
            flood = {"ok": False, "error_code": 429, "description": "Too Many Requests"}
            return web.json_response({**flood, "parameters": {"retry_after": 8}}, status=429)
        chat = {"id": -100, "type": "group", "title": "g"}
        return web.json_response({"ok": True, "result": {"message_id": 7, "date": 1, "chat": chat}})


async def test_built_bot_uploads_the_video_again_after_a_429(sleep):
    """End to end through aiogram: the second upload must carry the whole file."""
    telegram = FakeTelegram()
    app = web.Application()
    app.router.add_post("/bot42:TOKEN/sendVideo", telegram.send_video)
    async with TestServer(app) as server:
        bot = build_bot("42:TOKEN", FloodRetryMiddleware(sleep=sleep))
        assert isinstance(bot.session, AiohttpSession)
        bot.session.api = TelegramAPIServer.from_base(str(server.make_url("/")))
        try:
            sent = await bot(
                SendVideo(chat_id=-100, video=BufferedInputFile(b"video-bytes", "media.mp4"))
            )
        finally:
            await bot.session.close()

    assert sent.message_id == 7
    assert sleep.waits == [8]
    assert telegram.uploads == [b"video-bytes", b"video-bytes"]
