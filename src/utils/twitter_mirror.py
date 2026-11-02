"""Point caption links at a live Nitter-style mirror instead of x.com.

Logged-out readers can't open most x.com posts, so captions linked to
xcancel.com until X's legal letters suspended it (Sept 2026) and every caption
link landed on a 451 page. Mirrors now come and go, so the bot probes an
ordered list in the background and captions read the cached pick: posting
never waits on a health check, and with no mirror up the link stays on x.com.

Usage::

    mirror = TwitterMirror(parse_hosts(settings.twitter_link_hosts))
    task = asyncio.create_task(mirror.keep_fresh(session))
    mirror.rewrite("https://x.com/dhh/status/1")  # -> https://<mirror>/dhh/status/1
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import structlog

logger = structlog.get_logger()

REFRESH_INTERVAL_SECONDS = 600

_TWITTER_HOSTS = frozenset(
    {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
)

# A long-lived public post: a status page goes through the mirror's backend (or
# the bot-check in front of it), where a static homepage could stay up alone.
_PROBE_PATH = "/jack/status/20"
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=5)
_SNIFF_BYTES = 8192
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)

# Every live mirror sits behind a JS bot-check (Anubis, Gandalf, Cloudflare)
# that a browser clears in a second but a server never can, so "up" here means
# "answers", not "renders the post". Anubis and Gandalf answer 200; Cloudflare
# answers 403/503 with this title and still means a human gets through.
_CLOUDFLARE_CHALLENGE = b"Just a moment"


def parse_hosts(raw: str) -> list[str]:
    """Split a comma-separated host list, tolerating schemes and slashes.

    >>> parse_hosts("https://nitter.a.com/, nitter.b.com")
    ['nitter.a.com', 'nitter.b.com']
    """
    hosts: list[str] = []
    for part in raw.split(","):
        host = part.strip().removeprefix("https://").removeprefix("http://").strip("/")
        if host:
            hosts.append(host)
    return hosts


class TwitterMirror:
    """An ordered mirror list plus the first host that answered the last probe.

    >>> mirror = TwitterMirror(["nitter.a.com"])
    >>> mirror.rewrite("https://x.com/a/status/1")  # nothing probed yet
    'https://x.com/a/status/1'
    """

    def __init__(self, hosts: list[str]) -> None:
        self.hosts = hosts
        self.active_host: str | None = None
        self._probed = False

    def rewrite(self, url: str) -> str:
        """Move an x.com/twitter.com URL onto the active mirror; else return it as is."""
        parts = urlsplit(url)
        if self.active_host is None or parts.hostname not in _TWITTER_HOSTS:
            return url
        return urlunsplit(parts._replace(scheme="https", netloc=self.active_host))

    async def refresh(self, session: aiohttp.ClientSession) -> None:
        """Probe every host at once and keep the first healthy one in list order."""
        healthy = await asyncio.gather(*(_is_up(session, host) for host in self.hosts))
        picked = next((host for host, ok in zip(self.hosts, healthy) if ok), None)
        if picked != self.active_host or not self._probed:
            _log_pick(picked, self.hosts, healthy)
        self.active_host = picked
        self._probed = True

    async def keep_fresh(
        self, session: aiohttp.ClientSession, interval_seconds: float = REFRESH_INTERVAL_SECONDS
    ) -> None:
        """Refresh now and every *interval_seconds* for as long as the task runs."""
        while True:
            try:
                await self.refresh(session)
            except Exception as exc:  # one bad cycle must not end the loop
                logger.error("twitter_mirror_refresh_failed", error=str(exc))
            await asyncio.sleep(interval_seconds)


async def _is_up(session: aiohttp.ClientSession, host: str) -> bool:
    try:
        async with session.get(
            f"https://{host}{_PROBE_PATH}",
            timeout=_PROBE_TIMEOUT,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            allow_redirects=True,
        ) as resp:
            if resp.status < 400:
                return True
            return _CLOUDFLARE_CHALLENGE in await resp.content.read(_SNIFF_BYTES)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return False


def _log_pick(picked: str | None, hosts: list[str], healthy: list[bool]) -> None:
    down = [host for host, ok in zip(hosts, healthy) if not ok]
    if picked is None:
        logger.warning("twitter_mirror_none_up", fallback="x.com", down=down)
    else:
        logger.info("twitter_mirror_selected", host=picked, down=down)
