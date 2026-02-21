from __future__ import annotations

from src.scrapers.base import ScrapedMedia


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
        xcancel_url = result.original_url.replace("x.com", "xcancel.com")
        parts.append(f'\n<a href="{xcancel_url}">Link</a>')

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
    """Truncate text to fit within Telegram caption limits."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
