from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.bot.filters import ContainsCommand


def _msg(text: str | None) -> MagicMock:
    m = MagicMock()
    m.text = text
    return m


@pytest.mark.parametrize(
    "text",
    [
        "/nocaption https://x.com/u/status/1",
        "https://x.com/u/status/1 /nocaption",
        "before /nocaption after",
        "/nocaption@MyBot https://x.com/u/status/1",
        "https://x.com/u/status/1 /nocaption@MyBot",
        "/nocaption",
    ],
)
async def test_contains_command_matches_anywhere(text):
    assert await ContainsCommand("nocaption")(_msg(text)) is True


@pytest.mark.parametrize(
    "text",
    [
        "no command here",
        "/nocaptionx https://x.com/u/status/1",  # token must end at boundary
        "x/nocaption",  # not preceded by whitespace
        "look at this/nocaption",
        "",
        None,
    ],
)
async def test_contains_command_rejects_non_matches(text):
    assert await ContainsCommand("nocaption")(_msg(text)) is False
