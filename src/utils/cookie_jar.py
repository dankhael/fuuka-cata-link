"""Persistent, rotation-aware cookie jar for yt-dlp runs.

Google rotates the YouTube session cookies (``__Secure-1PSIDTS`` and friends)
on every use and soon rejects the previous values. yt-dlp saves the rotated
cookies into whatever ``--cookies`` file it was given — so handing it a fresh
throwaway copy of the mounted jar on every run threw each rotation away, and
the read-only original went stale after a handful of requests ("The provided
YouTube account cookies are no longer valid").

The store keeps one *live* jar in a writable location. Each run gets a private
copy of the live jar (concurrent yt-dlp processes must not share a file), and
merges whatever the run rotated back into the live jar afterwards.

    jar = await checkout(tmpdir, settings.cookies_file)   # path for --cookies
    ...run yt-dlp...
    await checkin(jar)                                    # keep the rotation
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import structlog

from src.config import settings

logger = structlog.get_logger()

_LIVE_SUFFIX = ".live"
_HTTPONLY_PREFIX = "#HttpOnly_"

# One writer at a time on the live jar; readers copy under the same lock so a
# checkout never observes a half-written file.
_lock = asyncio.Lock()


def live_jar_path(source: str) -> Path:
    """Where the rotated copy of *source* lives (inside the persistent volume).

    >>> live_jar_path("/app/cookies.txt").name
    'cookies.txt.live'
    """
    return Path(settings.cookies_state_dir) / (Path(source).name + _LIVE_SUFFIX)


def _seed_if_stale(source: Path, live: Path) -> None:
    """(Re)create the live jar from *source* on first use or after the operator
    dropped a fresh export (a newer mtime on the mounted file)."""
    if live.exists() and live.stat().st_mtime >= source.stat().st_mtime:
        return
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, live)
    logger.info("cookie_jar_seeded", source=str(source), live=str(live))


async def checkout(tmpdir: str, source: str | None) -> str | None:
    """Return a private, writable copy of the live jar for one yt-dlp run.

    ``None`` when no cookies are configured. The copy sits in a subdirectory
    of *tmpdir* so yt-dlp's rewritten jar is never mistaken for downloaded
    media by callers globbing the output directory.
    """
    if not source:
        return None
    live = live_jar_path(source)
    private = Path(tmpdir) / "_cookies" / "cookies.txt"
    private.parent.mkdir(parents=True, exist_ok=True)
    async with _lock:
        _seed_if_stale(Path(source), live)
        shutil.copyfile(live, private)
    _remember(private, live)
    return str(private)


async def checkin(private_jar: str) -> None:
    """Merge cookies rotated during a run (written by yt-dlp into
    *private_jar*) back into the live jar. Missing file = the run never got
    far enough to save; nothing to keep."""
    private = Path(private_jar)
    if not private.exists():
        return
    live = _live_for(private)
    if live is None:
        return
    async with _lock:
        merged = merge_netscape_jars(live.read_text(encoding="utf-8"), private.read_text("utf-8"))
        tmp = live.with_suffix(live.suffix + ".tmp")
        tmp.write_text(merged, encoding="utf-8")
        os.replace(tmp, live)


_checked_out: dict[str, Path] = {}


def _live_for(private: Path) -> Path | None:
    return _checked_out.pop(str(private), None)


def _remember(private: Path, live: Path) -> None:
    _checked_out[str(private)] = live


def _cookie_key(line: str) -> tuple[str, str, str] | None:
    """(domain, path, name) identity of a Netscape cookie line, or ``None`` for
    comments/blank lines. yt-dlp marks HttpOnly cookies with a ``#HttpOnly_``
    domain prefix — that is the same cookie, so the prefix is not part of the key."""
    if not line.strip() or (line.startswith("#") and not line.startswith(_HTTPONLY_PREFIX)):
        return None
    fields = line.rstrip("\n").split("\t")
    if len(fields) < 7:
        return None
    domain = fields[0].removeprefix(_HTTPONLY_PREFIX)
    return domain, fields[2], fields[5]


def merge_netscape_jars(live_text: str, rotated_text: str) -> str:
    """Overlay *rotated_text*'s cookies on *live_text*, keyed by domain/path/name.

    Cookies only present in the live jar (other platforms' sessions) are kept;
    cookies the run rotated or added win. Comment lines come from the live jar.

    >>> live = "# Netscape HTTP Cookie File\\n.a.com\\tTRUE\\t/\\tTRUE\\t0\\tSID\\told\\n"
    >>> rotated = ".a.com\\tTRUE\\t/\\tTRUE\\t0\\tSID\\tnew\\n"
    >>> merge_netscape_jars(live, rotated).splitlines()[-1].endswith("new")
    True
    """
    rotated: dict[tuple[str, str, str], str] = {}
    for line in rotated_text.splitlines(keepends=True):
        key = _cookie_key(line)
        if key is not None:
            rotated[key] = line if line.endswith("\n") else line + "\n"

    out: list[str] = []
    for line in live_text.splitlines(keepends=True):
        key = _cookie_key(line)
        if key is None:
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        out.append(rotated.pop(key, line if line.endswith("\n") else line + "\n"))
    out.extend(rotated.values())
    return "".join(out)
