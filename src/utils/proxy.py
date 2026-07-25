"""Helpers for the optional per-platform outbound proxy.

Two concerns live here: telling whether the configured proxy is actually up
(``is_proxy_reachable``) and keeping its credentials out of the logs
(``redact_proxy_credentials``).
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger()

# yt-dlp/urllib never name the proxy when the proxy host is down: a refused
# SOCKS port surfaces as a bare, OS-localized "connection refused" (verified on
# yt-dlp 2026.03.17). So proxy outages are detected by probing the socket, not
# by pattern-matching error text.
_PROBE_TIMEOUT_SECONDS = 3.0

_DEFAULT_PROXY_PORTS = {
    "http": 80,
    "https": 443,
    "socks4": 1080,
    "socks4a": 1080,
    "socks5": 1080,
    "socks5h": 1080,
}

# The password is matched greedily up to the *last* "@" of the authority, so an
# unescaped "@" inside the password can't leave a fragment of it in the log.
_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<user>[^/\s:@]+):(?P<password>[^/\s]*)@"
)


def redact_proxy_credentials(text: str) -> str:
    """Mask the password of any ``scheme://user:password@host`` URL in *text*.

    Proxy URLs carry a SOCKS password and reach WARNING-level logs, which are
    persisted to ``logs/errors.log`` and copied off the VPS.

    >>> redact_proxy_credentials("socks5h://botproxy:hunter2@10.0.0.1:1080")
    'socks5h://botproxy:***@10.0.0.1:1080'
    """
    return _CREDENTIALS_RE.sub(lambda m: f"{m['scheme']}{m['user']}:***@", text)


def _proxy_endpoint(proxy_url: str) -> tuple[str, int]:
    """Split *proxy_url* into ``(host, port)``, applying the scheme's default port."""
    parts = urlsplit(proxy_url)
    if not parts.hostname:
        raise ValueError(
            f"unparseable proxy url: {redact_proxy_credentials(proxy_url)!r} "
            "(expected e.g. 'socks5h://host:1080')"
        )
    port = parts.port or _DEFAULT_PROXY_PORTS.get(parts.scheme.lower())
    if port is None:
        raise ValueError(
            f"proxy url has no port and its scheme has no default: "
            f"{redact_proxy_credentials(proxy_url)!r} "
            f"(schemes with a default: {sorted(_DEFAULT_PROXY_PORTS)})"
        )
    return parts.hostname, port


async def is_proxy_reachable(proxy_url: str, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """Return True if a TCP connection to *proxy_url*'s host:port succeeds.

    Only the transport is probed — no SOCKS handshake — because the caller just
    needs to know whether routing through the proxy is worth attempting. A
    malformed URL is reported as unreachable so a typo degrades to a direct
    connection instead of raising.

    >>> await is_proxy_reachable("socks5h://127.0.0.1:9")
    False
    """
    try:
        host, port = _proxy_endpoint(proxy_url)
    except ValueError as exc:
        logger.warning("proxy_url_invalid", error=str(exc))
        return False

    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False

    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass  # the connect already answered the question; teardown errors are noise
    return True
