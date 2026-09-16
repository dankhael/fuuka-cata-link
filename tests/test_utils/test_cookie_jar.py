from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import cookie_jar
from src.utils.cookie_jar import checkin, checkout, live_jar_path, merge_netscape_jars

HEADER = "# Netscape HTTP Cookie File\n"


def _cookie(name: str, value: str, domain: str = ".youtube.com") -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t2000000000\t{name}\t{value}\n"


@pytest.fixture
def state_dir(tmp_path):
    with patch.object(cookie_jar, "settings") as cfg:
        cfg.cookies_state_dir = str(tmp_path / "state")
        yield tmp_path / "state"


@pytest.fixture
def mounted_jar(tmp_path) -> Path:
    jar = tmp_path / "cookies.txt"
    jar.write_text(HEADER + _cookie("SID", "sid-v1") + _cookie("__Secure-1PSIDTS", "ts-v1"))
    return jar


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_replaces_rotated_values_and_keeps_the_rest():
    live = HEADER + _cookie("SID", "old") + _cookie("sessionid", "ig", domain=".instagram.com")
    rotated = _cookie("SID", "new")

    merged = merge_netscape_jars(live, rotated)

    assert _cookie("SID", "new") in merged
    assert _cookie("sessionid", "ig", domain=".instagram.com") in merged
    assert merged.startswith(HEADER)


def test_merge_appends_cookies_the_run_added():
    merged = merge_netscape_jars(HEADER + _cookie("SID", "x"), _cookie("NEW", "y"))
    assert _cookie("NEW", "y") in merged


def test_merge_treats_httponly_prefix_as_the_same_cookie():
    """yt-dlp writes HttpOnly cookies as '#HttpOnly_.youtube.com' — the same
    cookie, not a second entry."""
    live = HEADER + _cookie("LOGIN_INFO", "old")
    rotated = _cookie("LOGIN_INFO", "new", domain="#HttpOnly_.youtube.com")

    merged = merge_netscape_jars(live, rotated)

    assert merged.count("LOGIN_INFO") == 1
    assert "\tnew\n" in merged


# ---------------------------------------------------------------------------
# checkout / checkin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkout_seeds_live_jar_from_the_mounted_one(state_dir, mounted_jar, tmp_path):
    private = await checkout(str(tmp_path / "run"), str(mounted_jar))

    assert Path(private).read_text() == mounted_jar.read_text()
    assert live_jar_path(str(mounted_jar)).read_text() == mounted_jar.read_text()
    assert Path(private).parent.name == "_cookies"


@pytest.mark.asyncio
async def test_checkout_without_cookies_returns_none(state_dir, tmp_path):
    assert await checkout(str(tmp_path), None) is None


@pytest.mark.asyncio
async def test_checkin_persists_the_rotation_for_the_next_run(state_dir, mounted_jar, tmp_path):
    """Regression: every run used to get a throwaway copy of the mounted jar,
    so the rotated __Secure-1PSIDTS never survived and YouTube killed the
    session within a few requests."""
    (tmp_path / "run1").mkdir()
    private = await checkout(str(tmp_path / "run1"), str(mounted_jar))
    rotated = HEADER + _cookie("SID", "sid-v1") + _cookie("__Secure-1PSIDTS", "ts-v2")
    Path(private).write_text(rotated)
    await checkin(private)

    (tmp_path / "run2").mkdir()
    next_private = await checkout(str(tmp_path / "run2"), str(mounted_jar))

    assert _cookie("__Secure-1PSIDTS", "ts-v2") in Path(next_private).read_text()
    assert _cookie("__Secure-1PSIDTS", "ts-v1") not in Path(next_private).read_text()
    # the read-only mount is never touched
    assert _cookie("__Secure-1PSIDTS", "ts-v1") in mounted_jar.read_text()


@pytest.mark.asyncio
async def test_fresh_export_on_the_mount_replaces_the_live_jar(state_dir, mounted_jar, tmp_path):
    """The operator fixes a dead session by dropping a new cookies.txt; a newer
    mtime on the mount must win over whatever rotation the live jar holds."""
    (tmp_path / "run1").mkdir()
    private = await checkout(str(tmp_path / "run1"), str(mounted_jar))
    Path(private).write_text(HEADER + _cookie("SID", "rotated-but-dead"))
    await checkin(private)

    mounted_jar.write_text(HEADER + _cookie("SID", "fresh-export"))
    future = time.time() + 60
    os.utime(mounted_jar, (future, future))

    (tmp_path / "run2").mkdir()
    next_private = await checkout(str(tmp_path / "run2"), str(mounted_jar))

    assert _cookie("SID", "fresh-export") in Path(next_private).read_text()


@pytest.mark.asyncio
async def test_checkin_of_a_missing_jar_is_a_noop(state_dir, tmp_path):
    await checkin(str(tmp_path / "never-written.txt"))
