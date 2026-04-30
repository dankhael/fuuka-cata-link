from __future__ import annotations

import pytest

from src.bot.handlers import _find_commands


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/nocaption https://x.com/u/status/1", {"nocaption"}),
        ("https://x.com/u/status/1 /nocaption", {"nocaption"}),
        ("before /nocaption after", {"nocaption"}),
        ("/nocaption@MyBot https://x.com/u/status/1", {"nocaption"}),
        ("https://x.com/u/status/1 /nocaption@MyBot", {"nocaption"}),
        ("/nocaption", {"nocaption"}),
        ("/noreply /nocaption https://x.com/u/status/1", {"noreply", "nocaption"}),
        ("/ignore /noreply /nocaption", {"ignore", "noreply", "nocaption"}),
        ("/nocaption /nocaption again", {"nocaption"}),
    ],
)
def test_find_commands_matches(text, expected):
    assert _find_commands(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no command here",
        "/nocaptionx https://x.com/u/status/1",  # token must end at boundary
        "x/nocaption",  # not preceded by whitespace
        "look at this/nocaption",
        "/unknowncmd https://x.com/u/status/1",
        "",
        None,
    ],
)
def test_find_commands_rejects_non_matches(text):
    assert _find_commands(text) == set()
