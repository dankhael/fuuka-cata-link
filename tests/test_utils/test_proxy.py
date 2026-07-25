from __future__ import annotations

import asyncio

import pytest

from src.utils.proxy import is_proxy_reachable, redact_proxy_credentials


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("socks5h://bot:hunter2@10.0.0.1:1080", "socks5h://bot:***@10.0.0.1:1080"),
        ("http://user:p@ss-word@proxy:8080", "http://user:***@proxy:8080"),
        ("socks5://10.0.0.1:1080", "socks5://10.0.0.1:1080"),  # nothing to hide
        ("socks5://bot@10.0.0.1:1080", "socks5://bot@10.0.0.1:1080"),  # no password
    ],
)
def test_redact_proxy_credentials(raw: str, expected: str):
    assert redact_proxy_credentials(raw) == expected


def test_redact_proxy_credentials_inside_a_longer_message():
    """yt-dlp echoes the proxy URL into its stderr, which we re-raise."""
    message = "yt-dlp download failed: unable to reach socks5h://bot:hunter2@host:1080 giving up"

    redacted = redact_proxy_credentials(message)

    assert "hunter2" not in redacted
    assert "socks5h://bot:***@host:1080" in redacted


@pytest.mark.asyncio
async def test_reachable_proxy_detected():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        assert await is_proxy_reachable(f"socks5h://127.0.0.1:{port}")


@pytest.mark.asyncio
async def test_refused_port_is_unreachable():
    """Port 9 (discard) is closed on the loopback of every CI box."""
    assert not await is_proxy_reachable("socks5://127.0.0.1:9", timeout=1.0)


@pytest.mark.asyncio
async def test_default_port_is_applied_per_scheme(monkeypatch):
    """A SOCKS URL without a port must probe 1080 rather than fail to parse."""
    probed: list[tuple[str, int]] = []

    async def record_connect(host, port):
        probed.append((host, port))
        raise ConnectionRefusedError

    monkeypatch.setattr("asyncio.open_connection", record_connect)

    assert not await is_proxy_reachable("socks5h://proxy.internal")
    assert probed == [("proxy.internal", 1080)]


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["", "not-a-url", "socks5://", "gopher://host"])
async def test_malformed_proxy_url_degrades_to_unreachable(malformed: str):
    """A typo must downgrade to a direct connection, never raise mid-extraction."""
    assert not await is_proxy_reachable(malformed)
