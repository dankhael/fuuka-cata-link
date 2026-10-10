from __future__ import annotations

import re

from src.scrapers.base import ScrapedMedia

_HTML_ATOM = re.compile(
    r"<([a-z]+)\b[^>]*>.*?</\1\s*>|<[^>]*>|&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);",
    re.IGNORECASE | re.DOTALL,
)


def format_caption(result: ScrapedMedia) -> str:
    """Build the Telegram caption for a scraped media result.

    Format:
        [Caption/Description]

        <a href="original_link">Link</a>
    """
    parts: list[str] = []

    if result.author:
        parts.append(f"{result.author}:")

    if result.caption:
        parts.append(result.caption)

    if result.has_media:
        # If the original url is from x.com, replace it with xcancel.com
        if result.original_url.startswith("https://x.com/"):
            result.original_url = result.original_url.replace("x.com/", "xcancel.com/")
        parts.append(f'\n<a href="{result.original_url}">Link</a>')

    return "\n".join(parts) if parts else result.original_url


def format_text_post(result: ScrapedMedia) -> str:
    """Format a text-only post (no source link embedded)."""
    parts: list[str] = []

    if result.author:
        parts.append(f"{result.author}:")

    if result.caption:
        parts.append(result.caption)

    return "\n".join(parts) if parts else "(no content)"


def truncate(text: str, max_len: int = 1024) -> str:
    """Limit captions without splitting entities or complete HTML elements.

    For example, ``truncate('abcd&amp;more', 10)`` returns ``'abcd...'``.
    """
    if max_len < 0:
        raise ValueError("max_len must be non-negative")
    if len(text) <= max_len:
        return text
    suffix = "." * min(3, max_len)
    cutoff = max_len - len(suffix)
    for match in _HTML_ATOM.finditer(text):
        if match.start() < cutoff < match.end():
            cutoff = match.start()
            break
    return text[:cutoff] + suffix
