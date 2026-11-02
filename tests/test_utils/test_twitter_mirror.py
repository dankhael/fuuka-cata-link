from __future__ import annotations

import asyncio

import aiohttp
import pytest

from src.utils.twitter_mirror import TwitterMirror, parse_hosts


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Answers each mirror host with a canned status/body, or raises for it.

    ``replies`` maps host -> FakeResponse | Exception. Records requested URLs.
    """

    def __init__(self, replies: dict[str, FakeResponse | Exception]) -> None:
        self._replies = replies
        self.requested: list[str] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.requested.append(url)
        host = url.split("/")[2]
        reply = self._replies[host]
        if isinstance(reply, Exception):
            raise reply
        return reply


ANUBIS_PAGE = b"<title>Making sure you&#39;re not a bot!</title>"
CLOUDFLARE_PAGE = b"<title>Just a moment...</title>"
SUSPENDED_PAGE = b"<title>XCancel service is suspended.</title>"


class TestParseHosts:
    def test_splits_and_cleans_entries(self):
        raw = " https://nitter.a.com/ , nitter.b.com,,http://twiiit.com "
        assert parse_hosts(raw) == ["nitter.a.com", "nitter.b.com", "twiiit.com"]

    def test_empty_disables_mirrors(self):
        assert parse_hosts("") == []
        assert parse_hosts(" , ") == []


class TestRewrite:
    def test_moves_x_and_twitter_links_onto_the_active_mirror(self):
        mirror = TwitterMirror(["nitter.a.com"])
        mirror.active_host = "nitter.a.com"

        assert mirror.rewrite("https://x.com/dhh/status/1?s=20") == (
            "https://nitter.a.com/dhh/status/1?s=20"
        )
        assert mirror.rewrite("https://twitter.com/dhh/status/1") == (
            "https://nitter.a.com/dhh/status/1"
        )

    def test_keeps_the_original_link_when_no_mirror_is_up(self):
        mirror = TwitterMirror(["nitter.a.com"])

        assert mirror.rewrite("https://x.com/dhh/status/1") == "https://x.com/dhh/status/1"

    def test_leaves_other_platforms_alone(self):
        mirror = TwitterMirror(["nitter.a.com"])
        mirror.active_host = "nitter.a.com"

        url = "https://www.instagram.com/reel/ABC/"
        assert mirror.rewrite(url) == url


class TestRefresh:
    async def test_picks_the_first_healthy_host_in_list_order(self):
        mirror = TwitterMirror(["down.com", "first-up.com", "second-up.com"])
        session = FakeSession(
            {
                "down.com": aiohttp.ClientConnectionError("refused"),
                "first-up.com": FakeResponse(200, ANUBIS_PAGE),
                "second-up.com": FakeResponse(200, b"<html>nitter</html>"),
            }
        )

        await mirror.refresh(session)

        assert mirror.active_host == "first-up.com"

    async def test_falls_back_to_x_when_every_host_is_down(self):
        """Regression: xcancel was hard-coded, so its suspension (HTTP 451)
        broke every caption link instead of falling back to x.com."""
        mirror = TwitterMirror(["xcancel.com", "slow.com", "forbidden.com"])
        mirror.active_host = "xcancel.com"  # was up on the previous probe
        session = FakeSession(
            {
                "xcancel.com": FakeResponse(451, SUSPENDED_PAGE),
                "slow.com": asyncio.TimeoutError(),
                "forbidden.com": FakeResponse(403, b"forbidden"),
            }
        )

        await mirror.refresh(session)

        assert mirror.active_host is None
        assert mirror.rewrite("https://x.com/a/status/1") == "https://x.com/a/status/1"

    async def test_cloudflare_challenge_counts_as_up(self):
        """A human clears the challenge in a second; only the server can't."""
        mirror = TwitterMirror(["twiiit.com"])
        session = FakeSession({"twiiit.com": FakeResponse(403, CLOUDFLARE_PAGE)})

        await mirror.refresh(session)

        assert mirror.active_host == "twiiit.com"

    async def test_probes_a_status_page_over_https(self):
        mirror = TwitterMirror(["nitter.a.com"])
        session = FakeSession({"nitter.a.com": FakeResponse(200)})

        await mirror.refresh(session)

        assert session.requested == ["https://nitter.a.com/jack/status/20"]

    async def test_a_malformed_host_is_just_down(self):
        mirror = TwitterMirror(["bad host", "nitter.a.com"])
        session = FakeSession(
            {"bad host": ValueError("invalid URL"), "nitter.a.com": FakeResponse(200)}
        )

        await mirror.refresh(session)

        assert mirror.active_host == "nitter.a.com"


@pytest.mark.asyncio
async def test_keep_fresh_survives_an_unexpected_probe_error():
    """The loop runs for the bot's lifetime; one bad cycle must not end it."""

    class FlakyMirror(TwitterMirror):
        def __init__(self) -> None:
            super().__init__(["nitter.a.com"])
            self.calls = 0

        async def refresh(self, session: aiohttp.ClientSession) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            if self.calls == 3:
                raise asyncio.CancelledError

    mirror = FlakyMirror()
    with pytest.raises(asyncio.CancelledError):
        await mirror.keep_fresh(FakeSession({}), interval_seconds=0)

    assert mirror.calls == 3
