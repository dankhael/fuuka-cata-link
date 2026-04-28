"""Error diagnostics and performance recording via structlog processors.

Two structlog processors that intercept existing log events and write them
to dedicated rotating files:

- ``error_diagnostics_processor`` — captures WARNING+ events → ``logs/errors.log``
- ``performance_processor`` — correlates per-request timing → ``logs/performance.log``
"""

from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import settings

# ---------------------------------------------------------------------------
# Rotating file writer
# ---------------------------------------------------------------------------


class RotatingFileWriter:
    """Append-only file writer with simple size-based rotation."""

    def __init__(self, path: Path, max_bytes: int, backup_count: int = 1) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, line: str) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line if line.endswith("\n") else line + "\n")
        self._maybe_rotate()

    def _maybe_rotate(self) -> None:
        try:
            if self._path.stat().st_size < self._max_bytes:
                return
        except FileNotFoundError:
            return

        for i in range(self._backup_count, 0, -1):
            src = self._path.with_suffix(f"{self._path.suffix}.{i}")
            if i == self._backup_count:
                src.unlink(missing_ok=True)
            else:
                dst = self._path.with_suffix(f"{self._path.suffix}.{i + 1}")
                if src.exists():
                    src.rename(dst)

        self._path.rename(self._path.with_suffix(f"{self._path.suffix}.1"))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_SKIP_KEYS = frozenset({"event", "level", "timestamp", "_record", "_from_structlog"})


def _format_kv(event_dict: dict[str, Any]) -> str:
    """Format event_dict fields as key=value pairs, skipping internal keys."""
    parts: list[str] = []
    for k, v in event_dict.items():
        if k in _SKIP_KEYS:
            continue
        parts.append(f"{k}={v}")
    return " ".join(parts)


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ---------------------------------------------------------------------------
# Error diagnostics processor
# ---------------------------------------------------------------------------

_error_writer: RotatingFileWriter | None = None


def _get_error_writer() -> RotatingFileWriter:
    global _error_writer  # noqa: PLW0603
    if _error_writer is None:
        _error_writer = RotatingFileWriter(
            path=Path(settings.error_log_file),
            max_bytes=settings.diagnostics_max_size_mb * 1024 * 1024,
        )
    return _error_writer


def error_diagnostics_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Write WARNING+ events to the error diagnostics file."""
    level = event_dict.get("level", "")
    if level in ("warning", "error", "critical"):
        ts = event_dict.get("timestamp", _timestamp_now())
        event = event_dict.get("event", "unknown")
        kv = _format_kv(event_dict)
        line = f"[{ts}] {level.upper():<8} {event}  {kv}"
        _get_error_writer().write(line)
    return event_dict


# ---------------------------------------------------------------------------
# Performance processor
# ---------------------------------------------------------------------------

_perf_writer: RotatingFileWriter | None = None


def _get_perf_writer() -> RotatingFileWriter:
    global _perf_writer  # noqa: PLW0603
    if _perf_writer is None:
        _perf_writer = RotatingFileWriter(
            path=Path(settings.performance_log_file),
            max_bytes=settings.diagnostics_max_size_mb * 1024 * 1024,
        )
    return _perf_writer


_perf_record: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_perf_record", default=None
)


def performance_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Accumulate per-request timing and flush on message_handled."""
    event = event_dict.get("event", "")

    if event == "message_received":
        _perf_record.set(
            {
                "timestamp": event_dict.get("timestamp", _timestamp_now()),
                "chat_id": event_dict.get("chat_id"),
                "links": [],
            }
        )
        return event_dict

    record = _perf_record.get(None)
    if record is None:
        return event_dict

    if event == "media_extracted":
        record["links"].append(
            {
                "platform": event_dict.get("platform", "?"),
                "method": event_dict.get("method", "?"),
                "extraction_ms": event_dict.get("duration_ms"),
                "media_count": event_dict.get("media_count", 0),
            }
        )
    elif event == "media_downloaded":
        if record["links"]:
            record["links"][-1]["download_ms"] = event_dict.get("duration_ms")
            record["links"][-1]["download_count"] = event_dict.get("count")
    elif event == "video_compressed":
        if record["links"]:
            record["links"][-1]["compress_ms"] = event_dict.get("duration_ms")
    elif event == "media_sent":
        if record["links"]:
            record["links"][-1]["send_ms"] = event_dict.get("duration_ms")
    elif event == "message_handled":
        total_ms = event_dict.get("duration_ms")
        ts = record.get("timestamp", _timestamp_now())

        for link in record["links"]:
            parts = [
                f"platform={link['platform']}",
                f"method={link.get('method', '?')}",
                f"media={link.get('media_count', 0)}",
                f"extraction={link.get('extraction_ms', '?')}ms",
                f"download={link.get('download_ms', '-')}ms",
            ]
            if "compress_ms" in link:
                parts.append(f"compress={link['compress_ms']}ms")
            parts.append(f"send={link.get('send_ms', '-')}ms")
            parts.append(f"total={total_ms}ms")

            _get_perf_writer().write(f"[{ts}] {' '.join(parts)}")

        _perf_record.set(None)

    return event_dict
